"""Artifact storage adapters.

The filesystem backend needs no extra dependency and doubles as the default for
local runs. S3/MinIO is optional and only imported when configured, keeping the
core install light.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, TypeVar, cast
from urllib.parse import quote, unquote

from acessilia_toolbox.core.artifact import ArtifactRef, ArtifactStore, shards
from acessilia_toolbox.core.errors import (
    ArtifactNotFoundError,
    ConfigurationError,
    InvalidInputError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes, fingerprint_stream
from acessilia_toolbox.core.input_memory import check_in_memory_size, read_in_memory
from acessilia_toolbox.core.provider import ProviderDescriptor

METADATA_SUFFIX = ".meta.json"
LOG = logging.getLogger(__name__)
T = TypeVar("T")
_STORAGE_READY_CACHE_TTL_SECONDS = 5.0
_STORAGE_READY_LOCK = threading.RLock()
_STORAGE_READY_CACHE: dict[int, tuple[object, float, bool]] = {}
FALLBACK_MODES = frozenset({"disabled", "local", "shared"})


def _copy_bounded(source: BinaryIO, destination: BinaryIO, max_bytes: int) -> None:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise InvalidInputError(f"artifact exceeds {max_bytes} bytes")
        destination.write(chunk)


def _atomic_write(target: Path, payload: bytes, *, exclusive: bool = False) -> None:
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            except OSError:
                # Keep the lock file: unlinking it could let concurrent writers
                # lock different inodes. Closing it releases the advisory lock.
                try:
                    import fcntl

                    with target.with_name(target.name + ".lock").open("a+b") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        if not target.exists():
                            temporary.replace(target)
                except (ImportError, OSError) as exc:
                    raise ProviderUnavailableError(
                        "filesystem metadata publication requires hard links or "
                        "shared advisory locks and atomic rename"
                    ) from exc
        else:
            temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


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
        return self.put_stream(BytesIO(payload), media_type=media_type, filename=filename)

    def put_stream(
        self, stream: BinaryIO, *, media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef:
        stream.seek(0)
        artifact_id = fingerprint_stream(stream)
        size = stream.tell()
        target = self._path(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        valid = False
        if target.exists():
            with target.open("rb") as existing:
                valid = fingerprint_stream(existing) == artifact_id
        if not valid:
            fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
            temporary = Path(name)
            try:
                stream.seek(0)
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(stream, out, length=1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        ref = ArtifactRef(artifact_id=artifact_id, media_type=media_type, size=size,
                          uri=target.as_uri(), storage_backend=self.backend, filename=filename)
        _atomic_write(target.with_suffix(target.suffix + METADATA_SUFFIX),
                      ref.model_dump_json().encode("utf-8"), exclusive=True)
        return self.stat(artifact_id)

    def copy_to(self, artifact_id: str, destination: BinaryIO, *, max_bytes: int) -> None:
        target = self._path(artifact_id)
        if not target.is_file():
            raise ArtifactNotFoundError(f"unknown artifact {artifact_id}", artifact_id=artifact_id)
        with target.open("rb") as source:
            _copy_bounded(source, destination, max_bytes)

    def get(self, artifact_id: str) -> bytes:
        target = self._path(artifact_id)
        if not target.is_file():
            raise ArtifactNotFoundError(f"unknown artifact {artifact_id}", artifact_id=artifact_id)
        return target.read_bytes()

    def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).is_file()

    def stat(self, artifact_id: str) -> ArtifactRef:
        target = self._path(artifact_id)
        if not target.is_file():
            raise ArtifactNotFoundError(f"unknown artifact {artifact_id}", artifact_id=artifact_id)
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
        target = (self.root / first / second / digest).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ConfigurationError("artifact path escapes storage root")
        return target


class S3ArtifactStore:
    """MinIO/S3 store. Requires the optional `storage` extra."""

    backend = "s3"

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise ConfigurationError(
                "S3 storage requires the 'storage' extra: pip install 'acessilia-toolbox[storage]'",
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
            # Keep outages below the API client's timeout so failover can run.
            # Explicit total attempts also prevents AWS retry environment settings
            # from extending the wait for an unavailable primary.
            config=Config(
                connect_timeout=3,
                read_timeout=5,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )

    def put(
        self,
        payload: bytes,
        *,
        media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef:
        return self._put(payload, fingerprint_bytes(payload), len(payload), media_type, filename)

    def put_stream(
        self, stream: BinaryIO, *, media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef:
        stream.seek(0)
        artifact_id = fingerprint_stream(stream)
        return self._put(stream, artifact_id, stream.tell(), media_type, filename)

    def _put(
        self, payload: bytes | BinaryIO, artifact_id: str, size: int,
        media_type: str, filename: str | None,
    ) -> ArtifactRef:
        key = self._key(artifact_id)
        metadata = {"filename-utf8": quote(filename, safe="")} if filename else {}
        for attempt in range(3):
            try:
                if not isinstance(payload, bytes):
                    payload.seek(0)
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=payload,
                    ContentType=media_type,
                    Metadata=metadata,
                    IfNoneMatch="*",
                    **({"ContentLength": size} if not isinstance(payload, bytes) else {}),
                )
                break
            except Exception as exc:
                response = getattr(exc, "response", None) or {}
                code = response.get("Error", {}).get("Code")
                if code in {"PreconditionFailed", "412"}:
                    return self.stat(artifact_id)
                if code in {"ConditionalRequestConflict", "409"} and attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise ProviderUnavailableError(f"object storage write failed: {exc}") from exc
        return ArtifactRef(
            artifact_id=artifact_id,
            media_type=media_type,
            size=size,
            uri=f"s3://{self.bucket}/{key}",
            storage_backend=self.backend,
            filename=filename,
        )

    def copy_to(self, artifact_id: str, destination: BinaryIO, *, max_bytes: int) -> None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._key(artifact_id))
            source = response["Body"]
            try:
                _copy_bounded(source, destination, max_bytes)
            finally:
                close = getattr(source, "close", None)
                if callable(close):
                    close()
        except InvalidInputError:
            raise
        except Exception as exc:
            self._raise_read_error(exc, artifact_id)

    def get(self, artifact_id: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._key(artifact_id))
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
            filename=(
                unquote(head["Metadata"]["filename-utf8"])
                if "filename-utf8" in (head.get("Metadata") or {})
                else (head.get("Metadata") or {}).get("filename")
            ),
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
            "404",
            "NoSuchKey",
            "NotFound",
        }

    @classmethod
    def _raise_read_error(cls, exc: Exception, artifact_id: str) -> None:
        if cls._is_missing(exc):
            raise ArtifactNotFoundError(
                f"unknown artifact {artifact_id}", artifact_id=artifact_id
            ) from exc
        raise ProviderUnavailableError(f"object storage read failed: {exc}") from exc


class FailoverArtifactStore:
    """Read from either backend; fallback writes require an explicit policy.

    ``shared`` declares a backup visible to every replica. ``local`` is for
    single-instance deployments and offers no cross-host availability.
    ``disabled`` preserves backup reads but propagates primary write failures.
    """

    backend = "failover"

    def __init__(
        self, primary: ArtifactStore, fallback: ArtifactStore, *, fallback_mode: str = "disabled"
    ) -> None:
        if fallback_mode not in FALLBACK_MODES:
            raise ConfigurationError("artifact fallback mode must be disabled, local or shared")
        self.primary = primary
        self.fallback = fallback
        self.fallback_mode = fallback_mode

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
            if self.fallback_mode == "disabled":
                LOG.warning("primary artifact store unavailable; fallback writes disabled: %s", exc)
                raise
            LOG.warning("primary artifact store unavailable; writing to fallback: %s", exc)
            return self.fallback.put(payload, media_type=media_type, filename=filename)

    def put_stream(
        self, stream: BinaryIO, *, media_type: str = "application/octet-stream",
        filename: str | None = None,
    ) -> ArtifactRef:
        try:
            put_stream = getattr(self.primary, "put_stream", None)
            if callable(put_stream):
                return cast(
                    ArtifactRef, put_stream(stream, media_type=media_type, filename=filename)
                )
            return self.primary.put(
                read_in_memory(stream), media_type=media_type, filename=filename
            )
        except ProviderUnavailableError as exc:
            if self.fallback_mode == "disabled":
                LOG.warning("primary artifact store unavailable; fallback writes disabled: %s", exc)
                raise
            LOG.warning("primary artifact store unavailable; writing to fallback: %s", exc)
            stream.seek(0)
            put_stream = getattr(self.fallback, "put_stream", None)
            if callable(put_stream):
                return cast(
                    ArtifactRef, put_stream(stream, media_type=media_type, filename=filename)
                )
            return self.fallback.put(
                read_in_memory(stream), media_type=media_type, filename=filename
            )

    def copy_to(self, artifact_id: str, destination: BinaryIO, *, max_bytes: int) -> None:
        def copy(store: ArtifactStore) -> None:
            destination.seek(0)
            destination.truncate()
            copy_to = getattr(store, "copy_to", None)
            if callable(copy_to):
                copy_to(artifact_id, destination, max_bytes=max_bytes)
            else:
                check_in_memory_size(store.stat(artifact_id).size)
                payload = store.get(artifact_id)
                check_in_memory_size(len(payload))
                if len(payload) > max_bytes:
                    raise InvalidInputError(f"artifact exceeds {max_bytes} bytes")
                destination.write(payload)

        try:
            copy(self.primary)
        except (ArtifactNotFoundError, ProviderUnavailableError) as exc:
            try:
                copy(self.fallback)
            except ArtifactNotFoundError:
                if isinstance(exc, ProviderUnavailableError):
                    raise exc from None
                raise

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


def storage_ready(store: ArtifactStore | None) -> bool:
    """Check storage without loading payloads, caching results for five seconds.

    A failover store is ready when either backend can serve writes. If only the
    fallback is available, readiness remains successful and logs the degraded
    primary so operators can distinguish failover from full health.
    """
    if store is None:
        LOG.warning("storage readiness failed: no artifact store is configured")
        return False

    with _STORAGE_READY_LOCK:
        now = time.monotonic()
        cached = _STORAGE_READY_CACHE.get(id(store))
        if cached is not None and cached[0] is store:
            checked_at, result = cached[1], cached[2]
            if now - checked_at < _STORAGE_READY_CACHE_TTL_SECONDS:
                return result

        try:
            result = _storage_ready_uncached(store)
        except Exception:
            LOG.warning(
                "storage readiness probe failed for backend %s",
                getattr(store, "backend", type(store).__name__),
                exc_info=True,
            )
            result = False

        _STORAGE_READY_CACHE[id(store)] = (store, time.monotonic(), result)
        for key, entry in tuple(_STORAGE_READY_CACHE.items()):
            if now - entry[1] >= _STORAGE_READY_CACHE_TTL_SECONDS * 12:
                _STORAGE_READY_CACHE.pop(key, None)
        return result


def _storage_ready_uncached(store: ArtifactStore) -> bool:
    if isinstance(store, FailoverArtifactStore):
        primary_ready = storage_ready(store.primary)
        fallback_ready = storage_ready(store.fallback)
        if not primary_ready and fallback_ready:
            LOG.warning(
                "primary artifact storage is unavailable; readiness is served by fallback"
            )
        return primary_ready or fallback_ready
    if isinstance(store, FilesystemArtifactStore):
        fd, path = tempfile.mkstemp(dir=store.root)
        os.close(fd)
        os.unlink(path)
        return True
    if isinstance(store, S3ArtifactStore):
        store._client.head_bucket(Bucket=store.bucket)
        # A fixed, immutable probe checks write permission on a cache miss
        # without accumulating objects or requiring DeleteObject permission.
        store.put(b"acessilia-toolbox readiness probe\n")
        return True
    LOG.warning(
        "storage readiness is unsupported for backend %s",
        getattr(store, "backend", type(store).__name__),
    )
    return False
