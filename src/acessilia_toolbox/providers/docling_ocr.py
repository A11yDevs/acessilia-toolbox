"""docling-serve OCR adapter.

Uses docling-serve with OCR pipeline enabled to extract text from
scanned documents and images, returning recognized text with per-item
confidence scores and bounding boxes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth
from acessilia_toolbox.providers.docling_support import (
    extract_page_number,
    page_height,
    top_left_bbox,
)
from acessilia_toolbox.providers.http_support import format_http_error

CONVERT_PATH = "/v1/convert/file"

DEFAULT_OCR_LANG = "pt-BR"

# ``ocr_lang`` is public as BCP 47 (RFC 5646), like ``language`` elsewhere.
# docling-serve reads a bare value as a code native to whichever OCR engine
# it selects (EasyOCR ``pt``, Tesseract ``por``, RapidOCR ``latin``...), and a
# BCP 47 tag only behind the ``iso:`` prefix (docling >= 2.127), which it
# canonicalizes and maps onto that engine. Sending ``iso:<tag>`` keeps the
# provider independent of the engine and of docling-serve's ``ocr_preset``.
# A bare ``pt-BR`` names no engine code: the conversion fails inside
# docling-serve, whose synchronous endpoint then answers only
# ``404 Task result not found``.
ISO_PREFIX = "iso:"
# Well-formed shape of an RFC 5646 language tag: a 2-8 letter primary subtag
# followed by subtags of 1-8 letters or digits (script, region, variants,
# extensions). Whether docling supports the language is docling's to decide.
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_MISSING_RESULT = "Task result not found"

VERSION_KEYS = ("docling-serve", "docling_serve_version", "version")
COMPONENT_KEYS = ("docling-serve", "docling", "docling-core", "docling-ibm-models", "docling-parse")


class DoclingOcrProvider:
    """Calls docling-serve with OCR enabled and returns recognized text."""

    supports_streaming_input = True

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        params = dict(parameters or {})
        ocr_lang = params.get("ocr_lang", DEFAULT_OCR_LANG)
        force_ocr = params.get("force_ocr", True)
        tags = self._language_tags(ocr_lang)
        docling_lang = [ISO_PREFIX + tag for tag in tags]

        with self._client() as client:
            raw = self._convert(client, payload, filename, media_type, force_ocr, docling_lang)
            ocr_result = self._extract_ocr(raw, language=tags[0])
            versions = self._server_versions(client)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=ocr_result,
            backend="docling",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=_pick_version(versions, self.descriptor.version),
            configuration={
                "extractor": "docling-serve",
                "base_url": self.base_url,
                "capability": capability_id,
                "force_ocr": force_ocr,
                "ocr_lang": ocr_lang,
                "component_versions": _components(versions),
                **params,
                "docling_ocr_lang": docling_lang,
            },
        )

    def versions(self) -> dict[str, str]:
        with self._client(timeout=10.0) as client:
            reported = self._server_versions(client)
        return {
            "provider": _pick_version(reported, self.descriptor.version),
            **_components(reported),
        }

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(self.descriptor.health_path)
                response.raise_for_status()
                return ProviderHealth(
                    provider=self.descriptor.id,
                    healthy=True,
                    version=_pick_version(
                        self._server_versions(client), self.descriptor.version
                    ),
                    checked_at=checked_at,
                )
        except Exception as exc:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                checked_at=checked_at,
            )

    def _language_tags(self, ocr_lang: Any) -> list[str]:
        """Validate the requested language(s) as BCP 47 tags.

        Accepts one tag (``pt-BR``) or a list of them; an ``iso:`` prefix the
        caller already wrote is tolerated. A malformed value is
        ``invalid_input``, raised before the conversion request.
        """
        values = ocr_lang if isinstance(ocr_lang, list) else [ocr_lang]
        if not values:
            raise InvalidInputError("ocr_lang must name at least one language")
        return [self._language_tag(value) for value in values]

    def _language_tag(self, value: Any) -> str:
        tag = value.strip() if isinstance(value, str) else ""
        if tag[: len(ISO_PREFIX)].lower() == ISO_PREFIX:
            tag = tag[len(ISO_PREFIX):]
        if not _LANGUAGE_TAG.match(tag):
            raise InvalidInputError(
                f"ocr_lang {value!r} is not a BCP 47 language tag (e.g. 'pt-BR')"
            )
        return tag

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.descriptor.timeout_seconds,
        )

    def _convert(
        self,
        client: httpx.Client,
        payload: bytes,
        filename: str,
        media_type: str,
        force_ocr: bool,
        ocr_lang: list[str],
    ) -> dict[str, Any]:
        try:
            response = client.post(
                CONVERT_PATH,
                files={"files": (filename, payload, media_type)},
                data={
                    "to_formats": ["json"],
                    "force_ocr": str(force_ocr).lower(),
                    "ocr_lang": ocr_lang,
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"docling-serve timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPStatusError as exc:
            message = format_http_error("docling-serve", exc.response)
            if exc.response.status_code == 404 and _MISSING_RESULT in message:
                message += (
                    f" (the conversion failed inside docling-serve; "
                    f"ocr_lang sent: {', '.join(ocr_lang)})"
                )
            raise ProviderExecutionError(
                message,
                provider=self.descriptor.id,
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"docling-serve unreachable at {self.base_url}",
                provider=self.descriptor.id,
            ) from exc

        result = response.json()
        if not isinstance(result, dict):
            raise ProviderExecutionError(
                "docling-serve returned unexpected payload",
                provider=self.descriptor.id,
            )
        return result

    def _server_versions(self, client: httpx.Client) -> dict[str, str]:
        try:
            response = client.get("/version")
            response.raise_for_status()
            data = response.json()
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _extract_ocr(self, raw: dict[str, Any], *, language: str) -> dict[str, Any]:
        """Extract OCR text items from docling-serve response.

        Each ``bbox`` is ``[left, top, right, bottom]`` with a top-left origin,
        as ``artifact/ocr-result@1`` declares: Docling's dict boxes are
        normalized with ``coord_origin`` and the page height. An item whose
        box cannot be placed (no provenance, or a bottom-left box on a page of
        unknown height) keeps its text with an empty ``bbox``, as before.
        """
        doc = raw.get("document") or {}
        json_content = doc.get("json_content") or {}

        items: list[dict[str, Any]] = []
        full_text_parts: list[str] = []

        for collection in ("texts", "groups"):
            for item in json_content.get(collection, []):
                text = (item.get("text") or "").strip()
                if not text:
                    continue

                label = item.get("label")
                if isinstance(label, dict):
                    label = label.get("value", "")
                label = str(label) if label else ""

                prov_list = item.get("prov") or []
                prov = prov_list[0] if prov_list else {}
                page = extract_page_number(prov, default=1)
                bbox = top_left_bbox(prov.get("bbox"), page_height(json_content, page))

                items.append({
                    "text": text,
                    "label": label,
                    "confidence": item.get("confidence", 1.0),
                    "page": page,
                    "bbox": list(bbox) if bbox is not None else [],
                })
                full_text_parts.append(text)

        return {
            "items": items,
            "item_count": len(items),
            "full_text": "\n".join(full_text_parts),
            "language": language,
        }


def _pick_version(versions: dict[str, str], fallback: str) -> str:
    for key in VERSION_KEYS:
        if versions.get(key):
            return versions[key]
    return fallback


def _components(versions: dict[str, str]) -> dict[str, str]:
    return {key: versions[key] for key in COMPONENT_KEYS if key in versions}
