"""Artifact storage adapters.

The filesystem backend needs no extra dependency and doubles as the default for
local runs. S3/MinIO is optional and only imported when configured, keeping the
core install light.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from acessilia_toolbox.core.artifact import ArtifactRef, ArtifactStore, shards
from acessilia_toolbox.core.errors import (
    ArtifactNotFoundError,
    ConfigurationError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor

METADATA_SUFFIX = ".meta.json"
LOG = logging.getLogger(__name__)
T = TypeVar("T")


class FilesystemArtifactStore:
    """Content-addressable store on a local or mounted filesystem."""

    backend = "filesystem"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        payload: bytes,
        *,
        media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef:
        artifact_id = fingerprint_bytes(payload)
        target = self._path(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Identical content yields an identical path, so a rewrite is a no-op.
        if not target.exists():
            target.write_bytes(payload)

        ref = ArtifactRef(
            artifact_id=artifact_id,
            media_type=media_type,
            size=len(payload),
            uri=target.as_uri(),
            storage_backend=self.backend,
            filename=filename,
        )
        target.with_suffix(target.suffix + METADATA_SUFFIX).write_text(
            ref.model_dump_json(), encoding="utf-8"
        )
        return ref

    def get(self, artifact_id: str) -> bytes:
        target = self._path(artifact_id)
        if not target.is_file():
            raise ArtifactNotFoundError(
                f"unknown artifact {artifact_id}", artifact_id=artifact_id
            )
        return target.read_bytes()

    def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).is_file()

    def stat(self, artifact_id: str) -> ArtifactRef:
        target = self._path(artifact_id)
        if not target.is_file():
            raise ArtifactNotFoundError(
                f"unknown artifact {artifact_id}", artifact_id=artifact_id
            )
        metadata = target.with_suffix(target.suffix + METADATA_SUFFIX)
        if metadata.is_file():
            return ArtifactRef.model_validate_json(metadata.read_text(encoding="utf-8"))
        return ArtifactRef(
            artifact_id=artifact_id,
            size=target.stat().st_size,
            uri=target.as_uri(),
            storage_backend=self.backend,
        )

    def _path(self, artifact_id: str) -> Path:
        first, second, digest = shards(artifact_id)
        return self.root / first / second / digest


class S3ArtifactStore:
    """MinIO/S3 store. Requires the optional `storage` extra."""

    backend = "s3"

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise ConfigurationError(
                "S3 storage requires the 'storage' extra: pip install "
                "'acessilia-toolbox[storage]'",
                provider=descriptor.id,
            ) from exc

        config = descriptor.config
        self.bucket = str(config.get("bucket") or "acessilia")
        self._client = boto3.client(
            "s3",
            endpoint_url=descriptor.endpoint,
            aws_access_key_id=config.get("access_key"),
            aws_secret_access_key=config.get("secret_key"),
            region_name=config.get("region", "us-east-1"),
        )

    def put(
        self,
        payload: bytes,
        *,
        media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef:
        artifact_id = fingerprint_bytes(payload)
        key = self._key(artifact_id)
        metadata = {"filename": filename} if filename else {}
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=payload,
                ContentType=media_type,
                Metadata=metadata,
            )
        except Exception as exc:
            raise ProviderUnavailableError(f"object storage write failed: {exc}") from exc

        return ArtifactRef(
            artifact_id=artifact_id,
            media_type=media_type,
            size=len(payload),
            uri=f"s3://{self.bucket}/{key}",
            storage_backend=self.backend,
            filename=filename,
        )

    def get(self, artifact_id: str) -> bytes:
        try:
            response = self._client.get_object(
                Bucket=self.bucket, Key=self._key(artifact_id)
            )
            body: bytes = response["Body"].read()
        except Exception as exc:
            self._raise_read_error(exc, artifact_id)
        return body

    def exists(self, artifact_id: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(artifact_id))
        except Exception as exc:
            if self._is_missing(exc):
                return False
            raise ProviderUnavailableError(f"object storage lookup failed: {exc}") from exc
        return True

    def stat(self, artifact_id: str) -> ArtifactRef:
        try:
            head: dict[str, Any] = self._client.head_object(
                Bucket=self.bucket, Key=self._key(artifact_id)
            )
        except Exception as exc:
            self._raise_read_error(exc, artifact_id)
        return ArtifactRef(
            artifact_id=artifact_id,
            media_type=head.get("ContentType", "application/octet-stream"),
            size=int(head.get("ContentLength", 0)),
            uri=f"s3://{self.bucket}/{self._key(artifact_id)}",
            storage_backend=self.backend,
            filename=(head.get("Metadata") or {}).get("filename"),
        )

    def _key(self, artifact_id: str) -> str:
        return "objects/" + "/".join(shards(artifact_id))

    @staticmethod
    def _is_missing(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        error = response.get("Error")
        return isinstance(error, dict) and str(error.get("Code")) in {
            "404", "NoSuchKey", "NotFound"
        }

    @classmethod
    def _raise_read_error(cls, exc: Exception, artifact_id: str) -> None:
        if cls._is_missing(exc):
            raise ArtifactNotFoundError(
                f"unknown artifact {artifact_id}", artifact_id=artifact_id
            ) from exc
        raise ProviderUnavailableError(f"object storage read failed: {exc}") from exc


class FailoverArtifactStore:
    """Write to a local backup during S3 outages; read from either backend."""

    backend = "failover"

    def __init__(self, primary: ArtifactStore, fallback: ArtifactStore) -> None:
        self.primary = primary
        self.fallback = fallback

    def put(
        self,
        payload: bytes,
        *,
        media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef:
        try:
            return self.primary.put(payload, media_type=media_type, filename=filename)
        except ProviderUnavailableError as exc:
            LOG.warning("primary artifact store unavailable; writing to fallback: %s", exc)
            return self.fallback.put(payload, media_type=media_type, filename=filename)

    def get(self, artifact_id: str) -> bytes:
        return self._read(self.primary.get, self.fallback.get, artifact_id)

    def stat(self, artifact_id: str) -> ArtifactRef:
        return self._read(self.primary.stat, self.fallback.stat, artifact_id)

    def exists(self, artifact_id: str) -> bool:
        try:
            if self.primary.exists(artifact_id):
                return True
        except ProviderUnavailableError as exc:
            if self.fallback.exists(artifact_id):
                return True
            raise exc from None
        return self.fallback.exists(artifact_id)

    def _read(
        self,
        primary_read: Callable[[str], T],
        fallback_read: Callable[[str], T],
        artifact_id: str,
    ) -> T:
        try:
            return primary_read(artifact_id)
        except (ArtifactNotFoundError, ProviderUnavailableError) as exc:
            try:
                return fallback_read(artifact_id)
            except ArtifactNotFoundError:
                if isinstance(exc, ProviderUnavailableError):
                    raise exc from None
                raise


def create_artifact_store(descriptor: ProviderDescriptor) -> Any:
    """Build the store matching a provider descriptor's transport."""
    if descriptor.transport == "s3":
        return S3ArtifactStore(descriptor)
    root = descriptor.config.get("root")
    if not root:
        raise ConfigurationError(
            f"filesystem storage provider {descriptor.id} needs a 'root' path",
            provider=descriptor.id,
        )
    return FilesystemArtifactStore(Path(str(root)))


def load_metadata(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))
