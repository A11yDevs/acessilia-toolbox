"""Private request binding for process-local operation joining, not cache keys."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import UploadFile

from acessilia_toolbox.core.artifact import shards
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError
from acessilia_toolbox.core.fingerprint import canonical_json, fingerprint_bytes, fingerprint_stream
from acessilia_toolbox.core.provider import ProviderRegistry


def upload_identity(
    file: UploadFile,
    *,
    max_bytes: int,
    default_filename: str | None,
) -> dict[str, Any]:
    """Hash in bounded chunks and leave the request-owned stream ready for execution."""
    file.file.seek(0)
    try:
        fingerprint = fingerprint_stream(file.file, max_bytes=max_bytes)
    finally:
        file.file.seek(0)
    return {
        "kind": "upload",
        "content": fingerprint,
        "filename": (file.filename or default_filename)
        if default_filename is not None
        else file.filename,
        "media_type": file.content_type or "application/octet-stream",
    }


def artifact_identity(artifact_id: str) -> dict[str, str]:
    # Retried references need neither a download nor available storage. Metadata
    # resolved for the first execution belongs to its retained outcome.
    return {"kind": "artifact", "artifact_id": "sha256:" + shards(artifact_id)[2]}


def request_fingerprint(context: Mapping[str, Any]) -> str:
    try:
        material = canonical_json({"request_identity_version": 1, **context}, allow_nan=False)
        return fingerprint_bytes(material.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise InvalidInputError("operation request must have a finite UTF-8 JSON identity") from exc


def execution_binding(
    capabilities: CapabilityRegistry,
    providers: ProviderRegistry,
    *,
    capability_id: str,
    provider_id: str | None,
    capability_version: int | None,
    source: Mapping[str, Any],
    parameters: Mapping[str, Any],
    language: str,
    no_cache: bool,
) -> tuple[str, str, int]:
    manifest = capabilities.get(capability_id, capability_version)
    descriptor = providers.resolve(manifest.id, provider_id)
    identity = request_fingerprint(
        {
            "route": "capability.execute",
            "capability": manifest.key,
            "contract": manifest.binding_data(),
            "provider": descriptor.model_dump(mode="json"),
            "input": source,
            "parameters": parameters,
            "language": language,
            "no_cache": no_cache,
        }
    )
    # Registries are deployment configuration, stable throughout a request.
    # Pin selections so execution does not resolve defaults again. Concurrent
    # registry mutation / hot reload is not a supported configuration mechanism.
    return identity, descriptor.id, manifest.version
