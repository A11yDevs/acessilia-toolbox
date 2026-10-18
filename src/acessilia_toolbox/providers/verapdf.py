"""veraPDF PDF/UA validation provider adapter.

Thin client of the out-of-process ``verapdf-validate`` sidecar, which owns the
pinned veraPDF release and reduces its report to the
``artifact/pdf-validation-report@1`` contract. A non-compliant PDF is a
successful result whose report lists the failed rules.
"""

from __future__ import annotations

import logging
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

LOG = logging.getLogger(__name__)

VALIDATE_PATH = "/validate"
VERSION_PATH = "/version"

# Everything the sidecar reports that shapes the verdict; all of it enters the
# cache key, so a veraPDF upgrade never reuses a report made by older rules.
VERSION_KEYS = ("version", "verapdf")

PROFILES = ("ua1", "ua2")
REPORT_KEYS = ("compliant", "profile", "profile_name", "statement", "summary", "failures")


class VeraPdfProvider:
    """Calls the verapdf-validate sidecar to check a PDF against PDF/UA."""

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
        params = dict(parameters or {})
        profile = params.get("profile", "ua1")
        if profile not in PROFILES:
            raise InvalidInputError(
                f"parameter 'profile' must be one of {list(PROFILES)}, got {profile!r}"
            )

        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        with self._client() as client:
            report = self._validate(client, payload, filename, profile)
            toolchain = self._server_versions(client)
        completed_at = datetime.now(UTC)
        duration_ms = int((perf_counter() - started_clock) * 1000)

        document = {key: report[key] for key in REPORT_KEYS}
        document["validator"] = {"name": "veraPDF", **toolchain}
        return ExtractionResult(
            document=document,
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            version=toolchain.get("version") or self.descriptor.version,
            configuration={"provider": self.descriptor.id, "profile": profile},
        )

    def _validate(
        self, client: httpx.Client, payload: bytes, filename: str, profile: str
    ) -> dict[str, Any]:
        try:
            response = client.post(
                VALIDATE_PATH,
                files={"file": (filename, payload, "application/pdf")},
                data={"profile": profile},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"PDF/UA validation timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"veraPDF validator is not reachable at {self.base_url}: {exc}",
                provider=self.descriptor.id,
            ) from exc

        if response.status_code in (400, 413, 422):
            # The file itself could not be validated: a caller error, not an outage.
            raise InvalidInputError(
                f"veraPDF rejected the PDF: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code == 504:
            raise ProviderTimeoutError(
                f"PDF/UA validation timed out in the validator: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code == 503:
            raise ProviderUnavailableError(
                f"veraPDF validator is not ready: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code != 200:
            raise ProviderExecutionError(
                f"PDF/UA validation failed with status {response.status_code}: {_detail(response)}",
                provider=self.descriptor.id,
                status_code=response.status_code,
            )
        try:
            report = response.json()
        except ValueError as exc:
            raise ProviderExecutionError(
                "veraPDF validator returned a body that is not JSON",
                provider=self.descriptor.id,
            ) from exc
        if not isinstance(report, dict):
            report = {}
        missing = [key for key in REPORT_KEYS if key not in report]
        if missing:
            raise ProviderExecutionError(
                f"veraPDF validator report lacks {missing}",
                provider=self.descriptor.id,
            )
        return dict(report)

    def versions(self) -> dict[str, str]:
        try:
            with self._client(timeout=5.0) as client:
                reported = self._server_versions(client)
        except Exception:
            # The executor tolerates version failures; the declared version is
            # then the only identity available for the cache key.
            LOG.debug("verapdf-validate version probe failed", exc_info=True)
            reported = {}
        return {"provider": reported.get("version") or self.descriptor.version, **reported}

    def _server_versions(self, client: httpx.Client) -> dict[str, str]:
        """Version fields the sidecar reports, filtered to verdict identity."""
        response = client.get(VERSION_PATH)
        if response.status_code != 200:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if k in VERSION_KEYS}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(self.descriptor.health_path or "/health")
                response.raise_for_status()
                data = response.json()
            version = (data.get("version") if isinstance(data, dict) else None) or (
                self.descriptor.version
            )
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


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])[:2000]
    return response.text[:500]
