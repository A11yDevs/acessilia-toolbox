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

# docling-serve hands ``ocr_lang`` to its OCR engine (EasyOCR by default),
# which wants its own language codes (``pt``, ``en``, ``ch_sim``), not BCP 47
# tags. An unknown code makes the conversion fail inside docling-serve, and its
# synchronous endpoint then answers only ``404 Task result not found``.
_ENGINE_LANGUAGE_EXCEPTIONS = {
    "zh-hans": "ch_sim",
    "zh-cn": "ch_sim",
    "zh-sg": "ch_sim",
    "zh-hant": "ch_tra",
    "zh-tw": "ch_tra",
    "zh-hk": "ch_tra",
}
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
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
        engine_lang = self._engine_languages(ocr_lang)

        with self._client() as client:
            raw = self._convert(client, payload, filename, media_type, force_ocr, engine_lang)
            ocr_result = self._extract_ocr(raw)
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
                "ocr_engine_lang": engine_lang,
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

    def _engine_languages(self, ocr_lang: Any) -> list[str]:
        """Translate the requested language(s) into OCR engine codes.

        Accepts one BCP 47 tag (``pt-BR``) or engine code (``pt``), or a list
        of them; a malformed value is ``invalid_input`` before docling-serve
        is called.
        """
        values = ocr_lang if isinstance(ocr_lang, list) else [ocr_lang]
        if not values:
            raise InvalidInputError("ocr_lang must name at least one language")
        return [self._engine_language(value) for value in values]

    def _engine_language(self, value: Any) -> str:
        if not isinstance(value, str) or not _LANGUAGE_TAG.match(value.strip()):
            raise InvalidInputError(
                f"ocr_lang {value!r} is not a language tag (e.g. 'pt-BR' or 'pt')"
            )
        tag = value.strip().lower()
        if tag in _ENGINE_LANGUAGE_EXCEPTIONS:
            return _ENGINE_LANGUAGE_EXCEPTIONS[tag]
        if "_" in tag and "-" not in tag:
            return tag  # already an engine code, e.g. ``ch_sim``
        return tag.split("-", 1)[0]

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
                    f"OCR languages sent: {', '.join(ocr_lang)})"
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

    def _extract_ocr(self, raw: dict[str, Any]) -> dict[str, Any]:
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
            "language": "pt-BR",
        }


def _pick_version(versions: dict[str, str], fallback: str) -> str:
    for key in VERSION_KEYS:
        if versions.get(key):
            return versions[key]
    return fallback


def _components(versions: dict[str, str]) -> dict[str, str]:
    return {key: versions[key] for key in COMPONENT_KEYS if key in versions}
