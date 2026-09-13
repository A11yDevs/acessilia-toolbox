"""Capability execution.

Validates the request against the capability manifest, resolves a provider,
runs it, normalizes the output and records provenance.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from acessilia_toolbox.core.artifact import ArtifactRef, ArtifactStore, ExecutionCache, NullCache
from acessilia_toolbox.core.cache import compute_cache_key
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import (
    ConfigurationError,
    InvalidInputError,
    UnsupportedMediaTypeError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes, fingerprint_parameters
from acessilia_toolbox.core.normalization import build_processing_manifest
from acessilia_toolbox.core.provenance import ExecutionProvenance
from acessilia_toolbox.core.provider import (
    ProviderAdapter,
    ProviderDescriptor,
    ProviderRegistry,
)

AdapterFactory = Callable[[ProviderDescriptor], ProviderAdapter]


class CapabilityResult(BaseModel):
    """Normalized execution result returned to consumers."""

    model_config = ConfigDict(extra="forbid")

    status: str = "succeeded"
    capability: str
    provider: str
    document: dict[str, Any]
    provenance: ExecutionProvenance
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class CapabilityExecutor:
    def __init__(
        self,
        capabilities: CapabilityRegistry,
        providers: ProviderRegistry,
        adapter_factory: AdapterFactory,
        *,
        cache: ExecutionCache | None = None,
        store: ArtifactStore | None = None,
    ) -> None:
        self._capabilities = capabilities
        self._providers = providers
        self._adapter_factory = adapter_factory
        self._cache = cache or NullCache()
        self._store = store

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        provider_id: str | None = None,
        capability_version: int | None = None,
        parameters: Mapping[str, Any] | None = None,
        language: str = "pt-BR",
    ) -> CapabilityResult:
        manifest = self._capabilities.get(capability_id, capability_version)
        _validate_input(manifest, payload, filename, media_type)

        descriptor = self._providers.resolve(manifest.id, provider_id)
        adapter = self._adapter_factory(descriptor)

        input_fingerprint = fingerprint_bytes(payload)
        versions = _versions_of(adapter)
        provider_version = versions.pop("provider", descriptor.version)
        cache_key = compute_cache_key(
            capability_id=manifest.id,
            capability_version=manifest.version,
            provider_id=descriptor.id,
            provider_version=provider_version,
            input_fingerprints=[input_fingerprint],
            parameters=parameters,
            model_versions=versions,
        )

        if manifest.execution.cacheable:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return _restore(cached, cache_key)

        extraction = adapter.execute(
            manifest.id,
            payload,
            filename=filename,
            media_type=media_type,
            parameters=parameters,
        )

        # Capabilities that return a plain dict (e.g. pdf.split, pdf.render)
        # skip the processing-manifest builder and use the document directly.
        if isinstance(extraction.document, dict):
            document_payload = extraction.document
            artifact_suffix = f"{Path(filename).stem}.json"
        else:
            # The builder reads the source from disk to derive size and digest, so
            # the in-memory payload is staged under its original name.
            with tempfile.TemporaryDirectory() as staging:
                source = Path(staging) / filename
                source.write_bytes(payload)
                document = build_processing_manifest(source, extraction, language=language)
            document_payload = document.model_dump(mode="json", by_alias=True)
            artifact_suffix = f"{Path(filename).stem}.structured-document.json"

        serialized = json.dumps(document_payload, ensure_ascii=False).encode("utf-8")

        artifacts: list[ArtifactRef] = []
        if self._store is not None:
            artifacts.append(
                self._store.put(
                    serialized,
                    media_type="application/json",
                    filename=artifact_suffix,
                )
            )
        else:
            artifacts.append(
                ArtifactRef.of(serialized, media_type="application/json")
            )

        result = CapabilityResult(
            capability=manifest.key,
            provider=descriptor.id,
            document=document_payload,
            artifacts=artifacts,
            provenance=ExecutionProvenance(
                capability=manifest.id,
                capability_version=manifest.version,
                provider=descriptor.id,
                provider_version=extraction.version,
                input_fingerprints=[input_fingerprint],
                output_fingerprints=[artifacts[0].artifact_id],
                parameters_hash=fingerprint_parameters(parameters),
                model_versions=versions,
                started_at=extraction.started_at,
                completed_at=extraction.completed_at,
                duration_ms=extraction.duration_ms,
                cache_key=cache_key,
            ),
        )

        if manifest.execution.cacheable:
            self._cache.put(cache_key, result.model_dump(mode="json"))
        return result

    def store_artifact(
        self, payload: bytes, *, media_type: str, filename: str | None = None
    ) -> ArtifactRef:
        return self._require_store().put(
            payload, media_type=media_type, filename=filename
        )

    def retrieve_artifact(self, artifact_id: str) -> tuple[bytes, ArtifactRef]:
        store = self._require_store()
        return store.get(artifact_id), store.stat(artifact_id)

    def _require_store(self) -> ArtifactStore:
        if self._store is None:
            raise ConfigurationError("no artifact storage provider is configured")
        return self._store


def _versions_of(adapter: ProviderAdapter) -> dict[str, str]:
    """Query provider versions, tolerating adapters that cannot report them."""
    try:
        return dict(adapter.versions())
    except Exception:
        return {}


def _restore(cached: dict[str, Any], cache_key: str) -> CapabilityResult:
    result = CapabilityResult.model_validate(cached)
    result.provenance.cache_hit = True
    result.provenance.cache_key = cache_key
    return result


def _validate_input(
    manifest: CapabilityManifest,
    payload: bytes,
    filename: str,
    media_type: str,
) -> None:
    if not payload:
        raise InvalidInputError("empty payload", capability=manifest.id)

    # Uploads are untrusted: reject path traversal before the name reaches disk.
    if not filename or filename != Path(filename).name or filename in {".", ".."}:
        raise InvalidInputError(f"unsafe filename: {filename!r}", capability=manifest.id)

    accepted = manifest.input.media_types
    if accepted and media_type not in accepted:
        raise UnsupportedMediaTypeError(
            f"{manifest.id} does not accept {media_type}",
            capability=manifest.id,
            media_type=media_type,
            accepted=accepted,
        )
