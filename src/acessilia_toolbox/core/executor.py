"""Capability execution.

Validates the request against the capability manifest, resolves a provider,
runs it, normalizes the output and records provenance.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from acessilia_toolbox.core.cache import compute_cache_key
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError, UnsupportedMediaTypeError
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


class CapabilityExecutor:
    def __init__(
        self,
        capabilities: CapabilityRegistry,
        providers: ProviderRegistry,
        adapter_factory: AdapterFactory,
    ) -> None:
        self._capabilities = capabilities
        self._providers = providers
        self._adapter_factory = adapter_factory

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

        extraction = adapter.execute(
            manifest.id,
            payload,
            filename=filename,
            media_type=media_type,
            parameters=parameters,
        )

        # The builder reads the source from disk to derive size and digest, so
        # the in-memory payload is staged under its original name.
        with tempfile.TemporaryDirectory() as staging:
            source = Path(staging) / filename
            source.write_bytes(payload)
            document = build_processing_manifest(source, extraction, language=language)

        input_fingerprint = fingerprint_bytes(payload)
        document_payload = document.model_dump(mode="json", by_alias=True)
        model_versions = _model_versions(extraction.configuration)

        return CapabilityResult(
            capability=manifest.key,
            provider=descriptor.id,
            document=document_payload,
            provenance=ExecutionProvenance(
                capability=manifest.id,
                capability_version=manifest.version,
                provider=descriptor.id,
                provider_version=extraction.version,
                input_fingerprints=[input_fingerprint],
                parameters_hash=fingerprint_parameters(parameters),
                model_versions=model_versions,
                started_at=extraction.started_at,
                completed_at=extraction.completed_at,
                duration_ms=extraction.duration_ms,
                cache_key=compute_cache_key(
                    capability_id=manifest.id,
                    capability_version=manifest.version,
                    provider_id=descriptor.id,
                    provider_version=extraction.version,
                    input_fingerprints=[input_fingerprint],
                    parameters=parameters,
                    model_versions=model_versions,
                ),
            ),
        )


def _model_versions(configuration: Mapping[str, Any]) -> dict[str, str]:
    """Component versions reported by the provider, used to invalidate cache."""
    components = configuration.get("component_versions")
    if not isinstance(components, Mapping):
        return {}
    return {str(key): str(value) for key, value in components.items()}


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
