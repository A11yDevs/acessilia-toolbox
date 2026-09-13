"""docling-serve layout analysis adapter.

Extracts and classifies page regions (text, image, table, formula, code, etc.)
from a document using docling-serve, returning a simplified layout analysis
without the full document structure.
"""

from __future__ import annotations

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

# Docling label to Acessilia layout classification mapping.
DOCLING_TO_LAYOUT = {
    "image": "embedded_image",
    "table": "table",
    "formula": "formula",
    "heading": "text_clean",
    "caption": "text_clean",
    "list": "list_block",
    "code": "code_block",
    "callout": "callout_box",
    "text": "text_clean",
}

TEXT_CLEAN_MIN_CHARS = 20
TEXT_CLEAN_MIN_DENSITY = 0.015
SCANNED_MAX_DENSITY = 0.005
IMAGE_CONFIDENCE_THRESHOLD = 0.5
UNKNOWN_MIN_AREA = 8000
FORMULA_MIN_AREA = 500


class DoclingLayoutProvider:
    """Calls docling-serve and returns a layout analysis with classified regions."""

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
            layout = self._build_layout(raw)
            versions = self._server_versions(client)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=layout,
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
                files={"file": (filename, payload, media_type)},
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

    def _build_layout(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Convert docling-serve response to a layout analysis document."""
        pages = raw.get("pages", {})
        if isinstance(pages, list):
            pages = {p.get("page_number", i): p for i, p in enumerate(pages)}

        page_regions: dict[int, list[dict[str, Any]]] = {}
        page_dimensions: dict[int, dict[str, float]] = {}

        # Collect items from all collections
        for collection in ("texts", "pictures", "tables", "groups"):
            for item in raw.get(collection, []):
                for prov in (item.get("prov") or []):
                    page_num = prov.get("page", 1)
                    bbox = prov.get("bbox", [])
                    if len(bbox) != 4:
                        continue

                    label = self._get_label(item)
                    classification = self._classify(item, label, bbox)

                    region = {
                        "type": classification,
                        "label": label,
                        "bbox": bbox,
                        "confidence": item.get("confidence", 1.0),
                        "text": item.get("text", ""),
                        "page_number": page_num,
                    }

                    if classification == "ignore":
                        continue

                    page_regions.setdefault(page_num, []).append(region)

        # Collect page dimensions
        for page_num_str, page_data in pages.items():
            page_num = int(page_num_str)
            if isinstance(page_data, dict):
                page_dimensions[page_num] = {
                    "width": page_data.get("width", 0),
                    "height": page_data.get("height", 0),
                }

        # Build page-level output
        pages_output = []
        for page_num in sorted(page_regions):
            regions = page_regions[page_num]
            dims = page_dimensions.get(page_num, {})
            pages_output.append({
                "page_number": page_num,
                "width": dims.get("width", 0),
                "height": dims.get("height", 0),
                "regions": regions,
            })

        region_count = 0
        for p in pages_output:
            region_count += len(cast(list[dict[str, Any]], p.get("regions", [])))
        return {
            "pages": pages_output,
            "page_count": len(pages_output),
            "region_count": region_count,
        }

    def _get_label(self, item: dict[str, Any]) -> str:
        """Extract the docling label from an item."""
        label_data = item.get("label", {})
        if isinstance(label_data, dict):
            return str(label_data.get("value", "unknown"))
        return str(label_data) if label_data else "unknown"

    def _classify(
        self, item: dict[str, Any], label: str, bbox: list[float]
    ) -> str:
        """Classify a docling item into an Acessilia layout category."""
        label_lower = label.lower()

        # Direct mapping from docling classification
        if label_lower in DOCLING_TO_LAYOUT:
            return DOCLING_TO_LAYOUT[label_lower]

        # Heuristic classification for text regions
        if label_lower == "text" or label_lower == "paragraph":
            text = item.get("text", "")
            total_chars = len(text.strip())
            area = self._bbox_area(bbox)
            text_density = total_chars / max(area, 1)

            if total_chars >= TEXT_CLEAN_MIN_CHARS and text_density >= TEXT_CLEAN_MIN_DENSITY:
                return "text_clean"
            if total_chars > 5 and text_density >= SCANNED_MAX_DENSITY:
                return "text_scanned"
            if area > UNKNOWN_MIN_AREA:
                return "unknown"
            return "ignore"

        # Image classification
        if label_lower == "picture" or label_lower == "image":
            confidence = item.get("confidence", 0)
            if confidence >= IMAGE_CONFIDENCE_THRESHOLD:
                return "embedded_image"
            if self._bbox_area(bbox) > UNKNOWN_MIN_AREA:
                return "unknown"
            return "ignore"

        # Formula
        if label_lower == "formula" or label_lower == "equation":
            if self._bbox_area(bbox) > FORMULA_MIN_AREA:
                return "formula"
            return "ignore"

        return "unknown"

    @staticmethod
    def _bbox_area(bbox: list[float]) -> float:
        if len(bbox) >= 4:
            return max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 0)
        return 0

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


def _pick_version(versions: dict[str, str], fallback: str) -> str:
    for key in VERSION_KEYS:
        if versions.get(key):
            return versions[key]
    return fallback


def _components(versions: dict[str, str]) -> dict[str, str]:
    return {key: versions[key] for key in COMPONENT_KEYS if key in versions}
