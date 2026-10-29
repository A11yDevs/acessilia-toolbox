"""docling-serve math recognition adapter.

Uses docling-serve to recognize mathematical expressions from images
and return LaTeX notation. Delegates HTTP transport to the shared
DoclingProvider helpers.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

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
            formulas = self._extract_formulas(raw)
            versions = self._server_versions(client)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=formulas,
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

    def _extract_formulas(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Extract LaTeX formulas from docling-serve response conforming to artifact/latex@1.

        Note on math.recognize@1:
            When multiple formulas are extracted from the document, the expressions are
            concatenated with double newlines (``\\n\\n``) in ``latex``, while ``page``
            and ``bbox`` anchor to the primary (first) detected formula.
        """
        doc = raw.get("document") or {}
        json_content = doc.get("json_content") or {}

        formulas: list[dict[str, Any]] = []

        # Extract from groups collection (formula/equation items)
        for item in json_content.get("groups", []):
            label = self._get_label(item)
            if label in ("formula", "equation"):
                latex = (item.get("text") or "").strip()
                if latex:
                    prov = (item.get("prov") or [{}])[0]
                    formulas.append({
                        "latex": latex,
                        "confidence": item.get("confidence"),
                        "page": prov.get("page", 1) or prov.get("page_no", 1),
                        "bbox": prov.get("bbox", []),
                    })

        # Also check texts with formula/equation label
        for item in json_content.get("texts", []):
            label = self._get_label(item)
            if label in ("formula", "equation"):
                latex = (item.get("text") or "").strip()
                if latex:
                    prov = (item.get("prov") or [{}])[0]
                    formulas.append({
                        "latex": latex,
                        "confidence": item.get("confidence"),
                        "page": prov.get("page", 1) or prov.get("page_no", 1),
                        "bbox": prov.get("bbox", []),
                    })

        # If no explicit formula elements, fallback to raw text if available
        if not formulas:
            raw_text = ""
            for item in json_content.get("texts", []):
                text = (item.get("text") or "").strip()
                if text:
                    raw_text += text + "\n"
            raw_text = raw_text.strip()
            if not raw_text:
                raise ProviderExecutionError(
                    "docling-serve found no mathematical formulas in the image",
                    provider=self.descriptor.id,
                )
            return {"latex": raw_text}

        latex_str = "\n\n".join(f["latex"] for f in formulas if f.get("latex")).strip()
        if not latex_str:
            raise ProviderExecutionError(
                "docling-serve found no mathematical formulas in the image",
                provider=self.descriptor.id,
            )

        primary = formulas[0]
        document_payload: dict[str, Any] = {
            "latex": latex_str,
        }

        raw_conf = primary.get("confidence")
        if raw_conf is not None:
            try:
                conf = float(raw_conf)
                if 0.0 <= conf <= 1.0:
                    document_payload["confidence"] = conf
            except (ValueError, TypeError):
                pass

        page = primary.get("page")
        if page is not None and isinstance(page, int) and page >= 1:
            document_payload["page"] = page

        bbox = self._bbox_values(primary.get("bbox"))
        if len(bbox) == 4:
            document_payload["bbox"] = bbox

        return document_payload

    @staticmethod
    def _bbox_values(bbox: Any) -> list[float]:
        """Normalize Docling dict/list coordinates to [left, top, right, bottom]."""
        if isinstance(bbox, dict):
            raw_coords = (
                bbox.get("l", bbox.get("left")),
                bbox.get("t", bbox.get("top")),
                bbox.get("r", bbox.get("right")),
                bbox.get("b", bbox.get("bottom")),
            )
            if all(v is not None for v in raw_coords):
                try:
                    left_f, top_f, right_f, bottom_f = (float(v) for v in raw_coords if v is not None)
                    return [
                        min(left_f, right_f),
                        min(top_f, bottom_f),
                        max(left_f, right_f),
                        max(top_f, bottom_f),
                    ]
                except (ValueError, TypeError):
                    return []
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                values = [float(v) for v in bbox[:4]]
                return [
                    min(values[0], values[2]),
                    min(values[1], values[3]),
                    max(values[0], values[2]),
                    max(values[1], values[3]),
                ]
            except (ValueError, TypeError):
                return []
        return []

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
