"""RapidOCR orientation detection and page correction provider adapter."""

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

ORIENT_PATH = "/orient"


class RapidOcrOrientProvider:
    """Calls out-of-process RapidOCR orientation service to detect/correct page orientation."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.descriptor.timeout_seconds,
        )

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
            raw = self._orient(client, payload, filename, media_type, parameters)

        completed_at = datetime.now(UTC)
        duration_ms = int((perf_counter() - started_clock) * 1000)

        angle = int(raw.get("angle", 0))
        confidence = float(raw.get("confidence", 0.0))
        auto_rotated = bool(raw.get("auto_rotated", False))
        stage = str(raw.get("stage", "unknown"))

        document_payload: dict[str, Any] = {
            "angle": angle,
            "confidence": confidence,
            "stage": stage,
            "auto_rotated": auto_rotated,
            "original_width": raw.get("original_width"),
            "original_height": raw.get("original_height"),
            "oriented_width": raw.get("oriented_width"),
            "oriented_height": raw.get("oriented_height"),
        }

        if "image_base64" in raw:
            document_payload["image_base64"] = raw["image_base64"]
            document_payload["media_type"] = raw.get("media_type", media_type)

        versions_map = self.versions()
        version = versions_map.get("engine_version", self.descriptor.version)
        configuration = {
            "provider": self.descriptor.id,
            "angle": angle,
            "auto_rotated": auto_rotated,
            "stage": stage,
        }

        return ExtractionResult(
            document=document_payload,
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            version=version,
            configuration=configuration,
        )

    def _orient(
        self,
        client: httpx.Client,
        payload: bytes,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        params = dict(parameters or {})
        return_image = bool(params.get("return_image", False))
        files = {
            "file": (filename, payload, media_type),
        }
        data = {
            "return_image": "true" if return_image else "false",
        }

        try:
            response = client.post(ORIENT_PATH, files=files, data=data)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"RapidOCR orientation request timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"RapidOCR orientation service is not reachable at {self.base_url}: {exc}",
                provider=self.descriptor.id,
            ) from exc

        if response.status_code != 200:
            raise ProviderExecutionError(
                f"RapidOCR orientation failed with status {response.status_code}: {response.text}",
                provider=self.descriptor.id,
            )

        try:
            result = response.json()
            if isinstance(result, dict):
                return result
            raise ProviderExecutionError(
                "RapidOCR orientation returned non-dict JSON response",
                provider=self.descriptor.id,
            )
        except Exception as exc:
            raise ProviderExecutionError(
                f"RapidOCR orientation returned non-JSON response: {response.text[:200]}",
                provider=self.descriptor.id,
            ) from exc

    def versions(self) -> dict[str, str]:
        reported: dict[str, str] = {}
        try:
            with self._client(timeout=5.0) as client:
                response = client.get("/version")
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict):
                        reported = {k: str(v) for k, v in data.items()}
        except Exception:
            pass

        server_version = (
            reported.get("engine_version")
            or reported.get("version")
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
                response = client.get(self.descriptor.health_path or "/health")
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
