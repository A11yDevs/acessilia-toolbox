"""Artifact identity and the storage/cache ports.

Artifacts are content-addressable: identity comes from the bytes, never from a
filename. Storage and cache live outside the toolbox, which only mediates
explicitly requested operations.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from acessilia_toolbox.core.errors import InvalidInputError
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



_CANONICAL_ID = re.compile(r"(?:sha256:)?([0-9a-f]{64})")


class ArtifactReference(BaseModel):
    """``artifact/reference@1`` (``schemas/reference@1.json``), the published contract.

    A projection of :class:`ArtifactRef`, the record the stores and the cache
    keep. Build it with :func:`reference_of`, the only producer, which
    guarantees what the schema cannot express: ``digest`` equals
    ``artifact_id``, both in canonical form, and ``store`` is never
    ``failover``. ``uri`` and ``filename`` stay in the record (A11yDevs/acessilia-toolbox#145).
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    size: int = Field(ge=0)
    store: str | None = Field(min_length=1)


def reference_of(ref: ArtifactRef) -> ArtifactReference:
    """The published reference for a stored record.

    The id is normalized to ``sha256:<hex>`` (a store's ``stat`` may echo an id
    given without the prefix), ``digest`` is that same value, and
    ``storage_backend`` becomes ``store``. ``None`` stays ``None``: the record
    claims no verified storage location.
    """
    match = _CANONICAL_ID.fullmatch(ref.artifact_id)
    if match is None:
        raise InvalidInputError("invalid SHA-256 artifact identifier")
    if ref.storage_backend == "failover":
        raise ValueError(
            "a reference names the backend that served it, never the failover wrapper"
        )
    canonical = f"sha256:{match.group(1)}"
    return ArtifactReference(
        artifact_id=canonical,
        digest=canonical,
        media_type=ref.media_type,
        size=ref.size,
        store=ref.storage_backend,
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
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", artifact_id):
        raise InvalidInputError("invalid SHA-256 artifact identifier")
    digest = artifact_id.removeprefix("sha256:")
    return digest[:2], digest[2:4], digest
