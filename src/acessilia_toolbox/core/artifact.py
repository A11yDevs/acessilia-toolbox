"""Artifact identity and the storage/cache ports.

Artifacts are content-addressable: identity comes from the bytes, never from a
filename. Storage and cache live outside the toolbox, which only mediates
explicitly requested operations.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from acessilia_toolbox.core.fingerprint import fingerprint_bytes


class ArtifactRef(BaseModel):
    """Reference to stored content; `uri` must never embed credentials."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    media_type: str = "application/octet-stream"
    size: int = Field(ge=0)
    uri: str | None = None
    storage_backend: str | None = None
    filename: str | None = None

    @classmethod
    def of(
        cls,
        payload: bytes,
        *,
        media_type: str = "application/octet-stream",
        filename: str | None = None,
        uri: str | None = None,
        storage_backend: str | None = None,
    ) -> ArtifactRef:
        return cls(
            artifact_id=fingerprint_bytes(payload),
            media_type=media_type,
            size=len(payload),
            uri=uri,
            storage_backend=storage_backend,
            filename=filename,
        )


@runtime_checkable
class ArtifactStore(Protocol):
    """Port for object storage: MinIO, S3 or the local filesystem."""

    backend: str

    def put(
        self,
        payload: bytes,
        *,
        media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef: ...

    def get(self, artifact_id: str) -> bytes: ...

    def exists(self, artifact_id: str) -> bool: ...

    def stat(self, artifact_id: str) -> ArtifactRef: ...


@runtime_checkable
class ExecutionCache(Protocol):
    """Port for the deterministic execution cache."""

    def get(self, key: str) -> dict[str, Any] | None: ...

    def put(self, key: str, value: dict[str, Any]) -> None: ...


class NullCache:
    """Disabled cache: every execution reaches the provider."""

    def get(self, key: str) -> dict[str, Any] | None:
        return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        return None


def shards(artifact_id: str) -> tuple[str, str, str]:
    """Split a digest into directory shards, keeping buckets browsable."""
    digest = artifact_id.removeprefix("sha256:")
    return digest[:2], digest[2:4], digest
