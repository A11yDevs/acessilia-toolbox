"""docling-serve VLM chemistry adapter.

Backs ``chem.recognize``: sends a picture to docling-serve running the VLM
pipeline (Granite Vision / granite-docling) and returns the model's
description of any chemical reaction found, normalized with the mhchem
normalizer when the model emits ``\\ce{}`` output.
"""

from __future__ import annotations

import re
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
from acessilia_toolbox.providers.pure_chem import normalize_mhchem

CONVERT_PATH = "/v1/convert/file"
VERSION_KEYS = ("docling-serve", "docling_serve_version", "version")

VERSION = "0.1.0"

# Prompt shipped alongside the picture so the VLM focuses on chemistry.
CHEM_PROMPT = (
    "Describe any chemical reaction or chemical equation in this image. "
    "If present, write it using mhchem LaTeX notation inside \\ce{...}."
)


class DoclingChemProvider:
    """Chemistry recognition via docling-serve VLM pipeline."""

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
        if capability_id != "chem.recognize":
            raise ValueError(f"Unsupported capability: {capability_id}")

        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        params = dict(parameters or {})
        prompt = str(params.get("prompt", CHEM_PROMPT))

        try:
            with self._client() as client:
                response = client.post(
                    CONVERT_PATH,
                    files={"files": (filename, payload, media_type)},
                    data={
                        "to_formats": ["md", "json"],
                        "pipeline": params.get("pipeline", "vlm"),
                        "prompt": prompt,
                    },
                )
                response.raise_for_status()
                body = response.json()
                versions = self._server_versions(client)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"docling-serve timed out after "
                f"{self.descriptor.timeout_seconds}s",
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

        if not isinstance(body, dict):
            raise ProviderExecutionError(
                "docling-serve returned an unexpected payload",
                provider=self.descriptor.id,
            )

        document = self._extract(body, prompt)
        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=document,
            backend="docling-vlm",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=self._pick(versions) or self.descriptor.version,
            configuration={
                "capability": capability_id,
                "prompt": prompt,
                **params,
            },
        )

    def _extract(self, body: dict[str, Any], prompt: str) -> dict[str, Any]:
        doc = body.get("document") or {}
        markdown = str(doc.get("md_content") or "")

        # Try to pull \ce{...} expressions out of the markdown first; they
        # are the most machine-readable outcome of a VLM chemistry pass.
        ce_pattern = r"\\ce\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        ce_matches = re.findall(ce_pattern, markdown)
        normalized = [normalize_mhchem(expr) for expr in ce_matches]

        description = markdown.strip() or None
        return {
            "description": description,
            "reactions": normalized,
            "reaction_count": len(normalized),
            "prompt": prompt,
        }

    def versions(self) -> dict[str, str]:
        with self._client(timeout=10.0) as client:
            versions = self._server_versions(client)
        return {"provider": self._pick(versions) or self.descriptor.version}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(self.descriptor.health_path)
                response.raise_for_status()
                return ProviderHealth(
                    provider=self.descriptor.id,
                    healthy=True,
                    version=self._pick(self._server_versions(client))
                    or self.descriptor.version,
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

    def _pick(self, versions: dict[str, str]) -> str | None:
        for key in VERSION_KEYS:
            if versions.get(key):
                return versions[key]
        return None


__all__ = ["CHEM_PROMPT", "DoclingChemProvider"]
