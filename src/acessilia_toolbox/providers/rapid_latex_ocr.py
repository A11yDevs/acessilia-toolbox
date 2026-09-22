"""rapid-latex-ocr math recognition adapter.

Uses a lightweight OCR service (e.g. RapidLaTeXOCR) to recognize mathematical
expressions from cropped formula images and return canonical LaTeX notation.
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

PREDICT_PATH = "/predict"


class RapidLatexOcrProvider:
    """Calls rapid-latex-ocr service to extract LaTeX from formula images."""

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
            raw = self._predict(client, payload, filename, media_type)
            versions = self._server_versions(client)

        completed_at = datetime.now(UTC)

        latex = raw.get("latex", "")
        raw_conf = raw.get("confidence")
        confidence = float(raw_conf) if raw_conf is not None else None
        bbox = raw.get("bbox")

        document_payload: dict[str, Any] = {
            "latex": latex,
        }
        if confidence is not None:
            document_payload["confidence"] = confidence
        if bbox is not None:
            document_payload["bbox"] = bbox

        server_version = (
            versions.get("version")
            or versions.get("rapid_latex_ocr")
            or self.descriptor.version
        )

        return ExtractionResult(
            document=document_payload,
            backend="rapid-latex-ocr",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=server_version,
            configuration={
                "extractor": "rapid-latex-ocr",
                "base_url": self.base_url,
                "capability": capability_id,
                **dict(parameters or {}),
            },
        )

    def versions(self) -> dict[str, str]:
        with self._client(timeout=10.0) as client:
            reported = self._server_versions(client)
        server_version = (
            reported.get("version")
            or reported.get("rapid_latex_ocr")
            or self.descriptor.version
        )
        return {
            "provider": server_version,
            **reported,
        }

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(self.descriptor.health_path)
                response.raise_for_status()
                is_json = response.headers.get("content-type", "").startswith("application/json")
                data = response.json() if is_json else {}
                version = (
                    data.get("version")
                    if isinstance(data, dict)
                    else None
                ) or self.descriptor.version
                return ProviderHealth(
                    provider=self.descriptor.id,
                    healthy=True,
                    version=version,
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

    def _predict(
        self, client: httpx.Client, payload: bytes, filename: str, media_type: str
    ) -> dict[str, Any]:
        try:
            response = client.post(
                PREDICT_PATH,
                files={"file": (filename, payload, media_type)},
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"rapid-latex-ocr timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderExecutionError(
                f"rapid-latex-ocr rejected: HTTP {exc.response.status_code}",
                provider=self.descriptor.id,
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"rapid-latex-ocr unreachable at {self.base_url}",
                provider=self.descriptor.id,
            ) from exc

        result = response.json()
        if not isinstance(result, dict):
            raise ProviderExecutionError(
                "rapid-latex-ocr returned unexpected payload",
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
