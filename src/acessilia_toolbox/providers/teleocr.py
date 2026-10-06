"""Optional HTTP adapter; TeleOCR inference runs in a separate GPU service."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth
from acessilia_toolbox.providers.teleocr_document import TeleOCRDocument


class TeleOCRParameters(BaseModel):
    min_long: int = Field(default=1600, ge=1, le=4096)
    max_long: int = Field(default=2400, ge=1, le=4096)
    batch_size: int = Field(default=8, ge=1, le=32)

    @model_validator(mode="after")
    def ordered_sizes(self) -> TeleOCRParameters:
        if self.min_long > self.max_long:
            raise ValueError("min_long cannot exceed max_long")
        return self


class TeleOCRProvider:
    """Transport plus validated shape mapping, with explicit provider selection."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout if timeout is not None else self.descriptor.timeout_seconds,
        )

    def _version(self, client: httpx.Client) -> dict[str, str]:
        try:
            response = client.get("/version")
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("model_revision"):
                return {
                    k: str(data[k]) for k in ("version", "model", "model_revision") if data.get(k)
                }
        except (httpx.HTTPError, ValueError):
            pass
        raise ProviderUnavailableError(
            "teleocr must report a model_revision at /version", provider=self.descriptor.id
        )

    def versions(self) -> dict[str, str]:
        try:
            with self._client(timeout=10) as client:
                reported = self._version(client)
        except ProviderUnavailableError:
            # The executor tolerates version failures. A unique identity prevents
            # it from restoring an old result while the GPU service is unavailable.
            reported = {"model_revision": f"unavailable:{uuid4().hex}"}
        defaults = {
            k: v for k, v in self.descriptor.config.items() if k in TeleOCRParameters.model_fields
        }
        return {
            "provider": reported.pop("version", self.descriptor.version),
            **reported,
            "inference_configuration": json.dumps(
                TeleOCRParameters.model_validate(defaults).model_dump(), sort_keys=True
            ),
        }

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10) as client:
                response = client.get(self.descriptor.health_path)
                response.raise_for_status()
                version = self._version(client).get("version", self.descriptor.version)
            return ProviderHealth(
                provider=self.descriptor.id, healthy=True, version=version, checked_at=checked_at
            )
        except (httpx.HTTPError, ValueError, ProviderUnavailableError) as exc:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                checked_at=checked_at,
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
        if capability_id != "document.structure.extract" or media_type not in {
            "image/jpeg",
            "image/png",
        }:
            raise ProviderExecutionError(
                "teleocr supports raster structure extraction only", provider=self.descriptor.id
            )
        try:
            defaults = {
                k: v
                for k, v in self.descriptor.config.items()
                if k in TeleOCRParameters.model_fields
            }
            config = TeleOCRParameters.model_validate({**defaults, **dict(parameters or {})})
        except ValidationError as exc:
            raise ProviderExecutionError(
                "invalid teleocr inference parameters", provider=self.descriptor.id
            ) from exc
        started_at = datetime.now(UTC)
        start = perf_counter()
        try:
            with self._client() as client:
                version = self._version(client)
                response = client.post(
                    "/predict",
                    files={"file": (filename, payload, media_type)},
                    data={"parameters": json.dumps(config.model_dump())},
                )
                response.raise_for_status()
                document = TeleOCRDocument(response.json())
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "teleocr inference timed out", provider=self.descriptor.id
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                "teleocr service is unreachable", provider=self.descriptor.id
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderExecutionError(
                "teleocr service rejected the request",
                provider=self.descriptor.id,
                status_code=exc.response.status_code,
            ) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise ProviderExecutionError(
                "invalid teleocr response shape", provider=self.descriptor.id
            ) from exc
        return ExtractionResult(
            document=document,
            backend="teleocr",
            started_at=started_at,
            completed_at=datetime.now(UTC),
            duration_ms=round((perf_counter() - start) * 1000),
            version=version.get("version", self.descriptor.version),
            configuration={
                "extractor": "teleocr",
                **config.model_dump(),
                **version,
                "infer_size": document.page.infer_size,
            },
        )
