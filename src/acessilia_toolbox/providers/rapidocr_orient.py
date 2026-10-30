"""RapidOCR orientation detection and page correction provider adapter."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Lock
from time import monotonic, perf_counter
from typing import Any

import httpx

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_parameters
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

LOG = logging.getLogger(__name__)

ORIENT_PATH = "/orient"
VERSION_PATH = "/version"

# `/version` reports the server build and the installed OCR engine. Only these
# describe the artifacts that produced a result, so only these enter provenance
# and the cache key.
VERSION_KEYS = ("version", "engine_version")
VERSION_TTL_SECONDS = 30.0
VERSION_RETRY_SECONDS = 5.0
_VERSION_CACHE_LIMIT = 128
_VERSION_CACHE: OrderedDict[tuple[str, str, str, str], tuple[float, dict[str, str]]] = OrderedDict()
_VERSION_REFRESHING: set[tuple[str, str, str, str]] = set()
_VERSION_LOCK = Lock()


class RapidOcrOrientProvider:
    """Calls out-of-process RapidOCR orientation service to detect/correct page orientation."""

    supports_streaming_input = True

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
        reported_versions = self._reported_versions()

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

        # The engine version identifies the model that produced the angle; the
        # server build is a weaker fallback when the engine cannot report one.
        version = (
            reported_versions.get("engine_version")
            or reported_versions.get("version")
            or self.descriptor.version
        )
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
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"RapidOCR orientation service is not reachable at {self.base_url}: {exc}",
                provider=self.descriptor.id,
            ) from exc

        if response.status_code != 200:
            raise ProviderExecutionError(
                f"RapidOCR orientation failed with status {response.status_code}: {response.text}",
                provider=self.descriptor.id,
                status_code=response.status_code,
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise ProviderExecutionError(
                f"RapidOCR orientation returned a non-JSON response: {response.text[:200]}",
                provider=self.descriptor.id,
            ) from exc

        if not isinstance(result, dict):
            raise ProviderExecutionError(
                f"RapidOCR orientation returned {type(result).__name__}; expected a JSON object",
                provider=self.descriptor.id,
            )
        return result

    def versions(self) -> dict[str, str]:
        reported = self._reported_versions()
        server_version = (
            reported.get("engine_version")
            or reported.get("version")
            or self.descriptor.version
        )
        return {
            "provider": server_version,
            **reported,
        }

    def _reported_versions(self) -> dict[str, str]:
        """Memoize across adapters; failed refreshes retain the last good identity.

        The lock only guards the cache, never the probe: while one request
        refreshes an expired entry, the others keep the last good value instead
        of waiting on a slow or unreachable ``/version``. The cache is bounded
        and keyed by deployment/configuration, so separate deployments cannot
        share versions. Orientation still runs on every auto-rotate request;
        only the metadata probe is memoized. Before the first success, declared
        versions apply.
        """
        key = (
            self.descriptor.id, self.base_url, self.descriptor.version,
            fingerprint_parameters(self.descriptor.config),
        )
        with _VERSION_LOCK:
            previous = _VERSION_CACHE.get(key)
            if previous is not None and (
                monotonic() < previous[0] or key in _VERSION_REFRESHING
            ):
                _VERSION_CACHE.move_to_end(key)
                return dict(previous[1])
            _VERSION_REFRESHING.add(key)
        try:
            try:
                with self._client(timeout=5.0) as client:
                    reported: dict[str, str] | None = self._server_versions(client)
            except Exception:
                LOG.debug("rapidocr-orient version probe failed", exc_info=True)
                reported = None
        finally:
            with _VERSION_LOCK:
                _VERSION_REFRESHING.discard(key)
        with _VERSION_LOCK:
            # A server answering without version fields is a valid answer, not
            # a failure: it keeps the full TTL instead of retrying every 5 s.
            ttl = VERSION_TTL_SECONDS
            if reported is None:
                current = _VERSION_CACHE.get(key, previous)
                reported = dict(current[1]) if current is not None else {}
                ttl = VERSION_RETRY_SECONDS
            _VERSION_CACHE[key] = (monotonic() + ttl, dict(reported))
            _VERSION_CACHE.move_to_end(key)
            while len(_VERSION_CACHE) > _VERSION_CACHE_LIMIT:
                _VERSION_CACHE.popitem(last=False)
            return dict(reported)

    def _server_versions(self, client: httpx.Client) -> dict[str, str]:
        """Version fields the server reports, filtered to artifact identity.

        Raises when the probe fails, so failure is distinct from a server that
        answers without reporting any version field.
        """
        response = client.get(VERSION_PATH)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"/version returned {type(data).__name__}; expected an object")
        return {k: str(v) for k, v in data.items() if k in VERSION_KEYS}

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
