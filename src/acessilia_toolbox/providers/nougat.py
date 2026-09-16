"""Nougat provider adapter.

Thin by design: transport plus shape mapping. Nougat runs out of process as an HTTP
service, so the toolbox image remains free of ML/PyTorch runtime dependencies.
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
from acessilia_toolbox.providers.nougat_document import NougatDocument

PREDICT_PATH = "/predict"


class NougatProvider:
    """Calls a Nougat service over HTTP and returns an extraction result."""

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
            document_data = self._predict(client, payload, filename, media_type)
            server_version = self._server_version(client)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=NougatDocument(document_data),
            backend="nougat",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=server_version or self.descriptor.version,
            configuration={
                "extractor": "nougat",
                "base_url": self.base_url,
                "capability": capability_id,
                **dict(parameters or {}),
            },
        )

    def versions(self) -> dict[str, str]:
        with self._client(timeout=10.0) as client:
            reported = self._server_version(client)
        return {
            "provider": reported or self.descriptor.version,
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
                    version=self._server_version(client) or self.descriptor.version,
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
            timeout=timeout if timeout is not None else self.descriptor.timeout_seconds,
        )

    def _predict(
        self, client: httpx.Client, payload: bytes, filename: str, media_type: str
    ) -> dict[str, Any] | str:
        files = {"file": (filename, payload, media_type)}
        try:
            response = client.post(PREDICT_PATH, files=files)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"nougat request timed out: {exc}", provider=self.descriptor.id
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"nougat unreachable: {exc}", provider=self.descriptor.id
            ) from exc

        if response.status_code >= 400:
            raise ProviderExecutionError(
                f"nougat error ({response.status_code}): {response.text[:200]}",
                provider=self.descriptor.id,
                status_code=response.status_code,
            )

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                data = response.json()
                if isinstance(data, dict):
                    return data
                if isinstance(data, str):
                    return {"text": data}
            except Exception:
                pass
        return {"text": response.text}

    def _server_version(self, client: httpx.Client) -> str:
        try:
            response = client.get("/version")
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    return str(data.get("version") or data.get("nougat_version") or "")
                return str(data)
        except Exception:
            pass
        return self.descriptor.version
