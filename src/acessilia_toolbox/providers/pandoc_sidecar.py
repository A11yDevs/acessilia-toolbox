"""Base adapter for the out-of-process Pandoc sidecar (``pdf-ua-render``).

The sidecar owns the pinned Pandoc (and, for PDF/UA, TeX Live) toolchain and
renders a Pandoc JSON AST into one output format per route. Subclasses name the
route, the output media type and the fields of ``/version`` that shape their
bytes. The rendered file is returned as a binary output, so the executor stores
it as an artifact and neither the cache nor the response carries the bytes.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, ClassVar

import httpx

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import BinaryOutput, ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

LOG = logging.getLogger(__name__)

VERSION_PATH = "/version"


class PandocSidecarProvider:
    """Calls one render route of the Pandoc sidecar."""

    supports_streaming_input = True

    #: Human name of the output, used in error messages.
    LABEL: ClassVar[str]
    RENDER_PATH: ClassVar[str]
    MEDIA_TYPE: ClassVar[str]
    SUFFIX: ClassVar[str]
    #: Leading bytes every valid output starts with.
    MAGIC: ClassVar[bytes]
    #: Fields of ``/version`` that shape the output; all of them enter the cache key.
    VERSION_KEYS: ClassVar[tuple[str, ...]]

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.descriptor.timeout_seconds,
        )

    def _document(self, content: bytes, toolchain: dict[str, str]) -> dict[str, Any]:
        """The JSON document returned next to the stored output."""
        return {
            "artifact_id": fingerprint_bytes(content),
            "media_type": self.MEDIA_TYPE,
            "size_bytes": len(content),
            "toolchain": toolchain,
        }

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
        lang = params.get("lang", "pt-BR")
        if not isinstance(lang, str) or not lang:
            raise InvalidInputError("parameter 'lang' must be a non-empty string")

        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        with self._client() as client:
            content = self._render(client, payload, filename, lang)
            toolchain = self._server_versions(client)
        completed_at = datetime.now(UTC)
        duration_ms = int((perf_counter() - started_clock) * 1000)

        stem = filename.rsplit(".", 1)[0] or "document"
        return ExtractionResult(
            document=self._document(content, toolchain),
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            version=toolchain.get("version") or self.descriptor.version,
            configuration={"provider": self.descriptor.id, "lang": lang},
            outputs=(BinaryOutput(content, self.MEDIA_TYPE, f"{stem}.{self.SUFFIX}"),),
        )

    def _render(self, client: httpx.Client, payload: bytes, filename: str, lang: str) -> bytes:
        label = self.LABEL
        try:
            response = client.post(
                self.RENDER_PATH,
                files={"file": (filename, payload, "application/json")},
                data={"lang": lang},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"{label} rendering timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"{label} renderer is not reachable at {self.base_url}: {exc}",
                provider=self.descriptor.id,
            ) from exc

        if response.status_code in (400, 413, 422):
            # The sidecar rejected the document itself: a caller error, not an outage.
            raise InvalidInputError(
                f"{label} renderer rejected the AST: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code == 504:
            raise ProviderTimeoutError(
                f"{label} rendering timed out in the renderer: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code == 503:
            raise ProviderUnavailableError(
                f"{label} renderer is not ready: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code != 200:
            raise ProviderExecutionError(
                f"{label} rendering failed with status {response.status_code}: {_detail(response)}",
                provider=self.descriptor.id,
                status_code=response.status_code,
            )
        if not response.content.startswith(self.MAGIC):
            raise ProviderExecutionError(
                f"{label} renderer returned a body that is not a {self.SUFFIX.upper()} file",
                provider=self.descriptor.id,
            )
        return response.content

    def versions(self) -> dict[str, str]:
        try:
            with self._client(timeout=5.0) as client:
                reported = self._server_versions(client)
        except Exception:
            # The executor tolerates version failures; the declared version is
            # then the only identity available for the cache key.
            LOG.debug("%s version probe failed", self.descriptor.id, exc_info=True)
            reported = {}
        return {"provider": reported.get("version") or self.descriptor.version, **reported}

    def _server_versions(self, client: httpx.Client) -> dict[str, str]:
        """Version fields the sidecar reports, filtered to output identity."""
        response = client.get(VERSION_PATH)
        if response.status_code != 200:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if k in self.VERSION_KEYS}

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
