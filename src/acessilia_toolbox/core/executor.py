"""Capability execution.

Validates the request against the capability manifest, resolves a provider,
runs it, normalizes the output and records provenance.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from acessilia_toolbox.core.artifact import (
    ArtifactRef,
    ArtifactStore,
    ExecutionCache,
    NullCache,
    shards,
)
from acessilia_toolbox.core.cache import compute_cache_key
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import (
    ConfigurationError,
    InvalidInputError,
    ToolboxError,
    UnsupportedMediaTypeError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes, fingerprint_parameters
from acessilia_toolbox.core.normalization import build_processing_manifest
from acessilia_toolbox.core.preprocessing import (
    OrientationResult,
    detect_and_orient_image,
    is_supported_image,
)
from acessilia_toolbox.core.provenance import ExecutionProvenance
from acessilia_toolbox.core.provider import (
    ProviderAdapter,
    ProviderDescriptor,
    ProviderRegistry,
)

LOG = logging.getLogger(__name__)

AdapterFactory = Callable[[ProviderDescriptor], ProviderAdapter]


class CapabilityResult(BaseModel):
    """Normalized execution result returned to consumers."""

    model_config = ConfigDict(extra="forbid")

    status: str = "succeeded"
    capability: str
    provider: str
    document: Any
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
        no_cache: bool = False,
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
            filename=filename,
            media_type=media_type,
            language=language,
        )

        if manifest.execution.cacheable and not no_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                try:
                    return _restore(cached, cache_key)
                except ValidationError:
                    # Cached results from an older contract must not block execution.
                    pass

        execution_payload = payload
        orientation: OrientationResult | None = None
        auto_rotate = bool(parameters and parameters.get("auto_rotate", False))
        if auto_rotate and is_supported_image(media_type):
            orient_adapter = None
            try:
                orient_desc = self._providers.resolve("image.page.orient")
                orient_adapter = self._adapter_factory(orient_desc)
            except ToolboxError as exc:
                # Orientation is an optional preprocessing step: a deployment
                # without the provider still extracts, just without rotating.
                LOG.info(
                    "auto_rotate requested but image.page.orient is unavailable: %s",
                    exc.message,
                )
            except Exception:
                LOG.warning(
                    "auto_rotate requested but the orientation provider could not be built",
                    exc_info=True,
                )
            execution_payload, orientation = detect_and_orient_image(
                payload, media_type=media_type, orient_provider=orient_adapter
            )
            if orientation.auto_rotated:
                # The rotated bytes are the real input, so they replace the
                # fingerprint; otherwise the cache could serve a stale result.
                input_fingerprint = fingerprint_bytes(execution_payload)

        extraction = adapter.execute(
            manifest.id,
            execution_payload,
            filename=filename,
            media_type=media_type,
            parameters=parameters,
        )

        # Capabilities that return a plain dict or list (e.g. pdf.split,
        # dataset.list) skip the processing-manifest builder and use the
        # document directly.
        if isinstance(extraction.document, (dict, list)):
            document_payload = extraction.document
            artifact_suffix = f"{Path(filename).stem}.json"
        else:
            # The builder reads the source from disk to derive size and digest, so
            # the in-memory payload is staged under its original name.
            with tempfile.TemporaryDirectory() as staging:
                source = Path(staging) / filename
                # Stage the bytes the provider actually saw, so `source.sha256`
                # matches the rotated input when auto-rotate applied.
                source.write_bytes(execution_payload)
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
            artifacts.append(ArtifactRef.of(serialized, media_type="application/json"))

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
                orientation=_orientation_payload(orientation),
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
        return self._require_store().put(payload, media_type=media_type, filename=filename)

    def retrieve_artifact(
        self, artifact_id: str, *, max_bytes: int | None = None
    ) -> tuple[bytes, ArtifactRef]:
        shards(artifact_id)
        store = self._require_store()
        ref = store.stat(artifact_id)
        if max_bytes is not None and ref.size > max_bytes:
            raise InvalidInputError(f"artifact exceeds {max_bytes} bytes")
        return store.get(artifact_id), ref

    def _require_store(self) -> ArtifactStore:
        if self._store is None:
            raise ConfigurationError("no artifact storage provider is configured")
        return self._store


def _orientation_payload(result: OrientationResult | None) -> dict[str, Any] | None:
    """Record the preprocessing decision so a rotated result stays auditable."""
    if result is None:
        return None
    return {
        "angle": result.angle,
        "confidence": result.confidence,
        "stage": result.stage,
        "auto_rotated": result.auto_rotated,
        "original_dimensions": list(result.original_dimensions),
        "oriented_dimensions": list(result.oriented_dimensions),
    }


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
