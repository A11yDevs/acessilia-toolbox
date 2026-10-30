"""docling-serve math recognition adapter.

Uses docling-serve to recognize mathematical expressions from images
and return LaTeX notation. Delegates HTTP transport to the shared
DoclingProvider helpers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

import httpx

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

CONVERT_PATH = "/v1/convert/file"

VERSION_KEYS = ("docling-serve", "docling_serve_version", "version")
COMPONENT_KEYS = ("docling-serve", "docling", "docling-core", "docling-ibm-models", "docling-parse")


class DoclingMathProvider:
    """Calls docling-serve and extracts LaTeX from formula groups."""

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

        with self._client() as client:
            raw = self._convert(client, payload, filename, media_type)
            document, notes = self._extract_formulas(raw)
            versions = self._server_versions(client)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=document,
            backend="docling",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=_pick_version(versions, self.descriptor.version),
            configuration={
                "extractor": "docling-serve",
                "base_url": self.base_url,
                "capability": capability_id,
                "component_versions": _components(versions),
                **notes,
                **dict(parameters or {}),
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

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.descriptor.timeout_seconds,
        )

    def _convert(
        self, client: httpx.Client, payload: bytes, filename: str, media_type: str
    ) -> dict[str, Any]:
        try:
            response = client.post(
                CONVERT_PATH,
                files={"files": (filename, payload, media_type)},
                data={"to_formats": ["json"]},
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"docling-serve timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderExecutionError(
                f"docling-serve rejected: HTTP {exc.response.status_code}",
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

    def _extract_formulas(
        self, raw: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build an artifact/latex@1 document from the docling-serve response.

        artifact/latex@1 carries a single expression, and math.convert and
        math.verbalize hand ``latex`` to MathJax/MathCAT as one expression, so
        only the first formula in reading order is returned. The number of
        formulas found is reported in the returned notes, which end up in the
        extraction configuration. Items without a ``formula``/``equation``
        label are never returned as LaTeX.
        """
        doc = raw.get("document") or {}
        json_content = doc.get("json_content") or {}

        formulas = [
            item
            for collection in ("groups", "texts")
            for item in json_content.get(collection, [])
            if self._get_label(item) in ("formula", "equation")
            and (item.get("text") or "").strip()
        ]
        if not formulas:
            raise ProviderExecutionError(
                "docling-serve found no mathematical formulas in the image",
                provider=self.descriptor.id,
            )

        primary = formulas[0]
        document_payload: dict[str, Any] = {"latex": primary["text"].strip()}
        notes: dict[str, Any] = {"formula_count": len(formulas)}

        raw_conf = primary.get("confidence")
        if raw_conf is not None:
            conf = _float_or_none(raw_conf)
            if conf is not None and 0.0 <= conf <= 1.0:
                document_payload["confidence"] = conf
            else:
                notes["dropped_confidence"] = str(raw_conf)

        prov = (primary.get("prov") or [{}])[0]
        page = prov.get("page_no") or prov.get("page") or 1
        if isinstance(page, int) and page >= 1:
            document_payload["page"] = page

        bbox = _top_left_bbox(prov.get("bbox"), _page_height(json_content, page))
        if bbox is not None:
            document_payload["bbox"] = list(bbox)

        return document_payload, notes

    @staticmethod
    def _get_label(item: dict[str, Any]) -> str:
        raw = item.get("label")
        if isinstance(raw, dict):
            return str(raw.get("value", ""))
        if isinstance(raw, str):
            return raw
        return ""


def _pick_version(versions: dict[str, str], fallback: str) -> str:
    for key in VERSION_KEYS:
        if versions.get(key):
            return versions[key]
    return fallback


def _components(versions: dict[str, str]) -> dict[str, str]:
    return {key: versions[key] for key in COMPONENT_KEYS if key in versions}


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _page_height(json_content: Mapping[str, Any], page: Any) -> float | None:
    pages = json_content.get("pages") or {}
    if not isinstance(pages, Mapping):
        return None
    entry = pages.get(str(page)) or pages.get(page) or {}
    if not isinstance(entry, Mapping):
        return None
    size = entry.get("size") or {}
    height = entry.get("height", size.get("height") if isinstance(size, Mapping) else None)
    return _float_or_none(height)


def _top_left_bbox(
    bbox: Any, page_height: float | None
) -> tuple[float, float, float, float] | None:
    """Normalize a Docling bbox to [left, top, right, bottom] with a top-left origin.

    Docling dict boxes carry ``coord_origin``; ``BOTTOMLEFT`` boxes are flipped
    against the page height and dropped when that height is unknown. List boxes
    have no origin and are taken as top-left.
    """
    origin = "TOPLEFT"
    if isinstance(bbox, Mapping):
        origin = str(bbox.get("coord_origin") or "TOPLEFT").upper()
        values = [
            bbox.get(short, bbox.get(long))
            for short, long in (("l", "left"), ("t", "top"), ("r", "right"), ("b", "bottom"))
        ]
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        values = list(bbox[:4])
    else:
        return None

    coords = [_float_or_none(value) for value in values]
    if any(value is None for value in coords):
        return None
    left, top, right, bottom = (cast(float, value) for value in coords)
    if origin == "BOTTOMLEFT":
        if page_height is None:
            return None
        top, bottom = page_height - top, page_height - bottom
    elif origin != "TOPLEFT":
        return None
    return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))
