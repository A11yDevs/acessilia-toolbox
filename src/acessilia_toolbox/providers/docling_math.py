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
        """Extract LaTeX formulas from docling-serve response.

        Returns a dict with:
        - formulas: list of {latex, confidence, page, bbox}
        - raw_text: full text content as fallback
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
                        "confidence": item.get("confidence", 1.0),
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
                        "confidence": item.get("confidence", 1.0),
                        "page": prov.get("page", 1) or prov.get("page_no", 1),
                        "bbox": prov.get("bbox", []),
                    })

        # Collect raw text as fallback
        raw_text = ""
        for item in json_content.get("texts", []):
            text = (item.get("text") or "").strip()
            if text:
                raw_text += text + "\n"

        return {
            "formulas": formulas,
            "raw_text": raw_text.strip(),
            "formula_count": len(formulas),
        }

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
