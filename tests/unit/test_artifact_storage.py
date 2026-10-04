"""Content-addressable artifact storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from acessilia_toolbox.api.app import _store_from
from acessilia_toolbox.core.artifact import ArtifactRef, NullCache, shards
from acessilia_toolbox.core.errors import (
    ArtifactNotFoundError,
    ConfigurationError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.storage import (
    FailoverArtifactStore,
    FilesystemArtifactStore,
    S3ArtifactStore,
    create_artifact_store,
)

PAYLOAD = b"%PDF-content"


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "objects")


def test_identity_comes_from_content_not_filename(store: FilesystemArtifactStore) -> None:
    first = store.put(PAYLOAD, filename="a.pdf")
    second = store.put(PAYLOAD, filename="completely-different.pdf")

    assert first.artifact_id == second.artifact_id == fingerprint_bytes(PAYLOAD)


def test_different_content_yields_different_identity(store: FilesystemArtifactStore) -> None:
    assert store.put(PAYLOAD).artifact_id != store.put(b"other").artifact_id


def test_stored_content_round_trips(store: FilesystemArtifactStore) -> None:
    ref = store.put(PAYLOAD, media_type="application/pdf", filename="a.pdf")

    assert store.get(ref.artifact_id) == PAYLOAD
    assert store.exists(ref.artifact_id)


def test_metadata_survives_retrieval(store: FilesystemArtifactStore) -> None:
    ref = store.put(PAYLOAD, media_type="application/pdf", filename="report.pdf")
    stat = store.stat(ref.artifact_id)

    assert stat.media_type == "application/pdf"
    assert stat.filename == "report.pdf"
    assert stat.size == len(PAYLOAD)
    assert stat.storage_backend == "filesystem"


def test_unknown_artifact_is_reported(store: FilesystemArtifactStore) -> None:
    missing = fingerprint_bytes(b"never stored")

    assert not store.exists(missing)
    with pytest.raises(ArtifactNotFoundError):
        store.get(missing)
    with pytest.raises(ArtifactNotFoundError):
        store.stat(missing)


def test_storing_identical_content_is_idempotent(
    store: FilesystemArtifactStore, tmp_path: Path
) -> None:
    ref = store.put(PAYLOAD)
    files_after_first = list((tmp_path / "objects").rglob("*"))
    store.put(PAYLOAD)

    assert list((tmp_path / "objects").rglob("*")) == files_after_first
    assert store.get(ref.artifact_id) == PAYLOAD


def test_objects_are_sharded_to_keep_directories_browsable(
    store: FilesystemArtifactStore, tmp_path: Path
) -> None:
    ref = store.put(PAYLOAD)
    first, second, digest = shards(ref.artifact_id)

    assert (tmp_path / "objects" / first / second / digest).is_file()


def test_reference_can_be_derived_without_storing() -> None:
    ref = ArtifactRef.of(PAYLOAD, media_type="application/pdf")

    assert ref.artifact_id == fingerprint_bytes(PAYLOAD)
    assert ref.size == len(PAYLOAD)
    assert ref.storage_backend is None


def test_factory_builds_a_filesystem_store(tmp_path: Path) -> None:
    descriptor = ProviderDescriptor.model_validate(
        {
            "id": "filesystem",
            "transport": "in_process",
            "capabilities": ["artifact.store"],
            "config": {"root": str(tmp_path)},
        }
    )

    assert isinstance(create_artifact_store(descriptor), FilesystemArtifactStore)


def test_factory_requires_a_root_for_filesystem_storage() -> None:
    descriptor = ProviderDescriptor.model_validate(
        {"id": "filesystem", "transport": "in_process", "capabilities": ["artifact.store"]}
    )

    with pytest.raises(ConfigurationError):
        create_artifact_store(descriptor)


class FakeS3Client:
    def __init__(self) -> None:
        self.available = True
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}

    def put_object(self, **kwargs: object) -> None:
        if not self.available:
            raise ConnectionError("MinIO offline")
        self.objects[str(kwargs["Key"])] = (
            bytes(kwargs["Body"]),
            str(kwargs["ContentType"]),
            dict(kwargs["Metadata"]),
        )

    def get_object(self, **kwargs: object) -> dict[str, object]:
        if not self.available:
            raise ConnectionError("MinIO offline")
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise MissingObjectError()
        return {"Body": ByteBody(self.objects[key][0])}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        if not self.available:
            raise ConnectionError("MinIO offline")
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise MissingObjectError()
        payload, media_type, metadata = self.objects[key]
        return {
            "ContentLength": len(payload),
            "ContentType": media_type,
            "Metadata": metadata,
        }


class MissingObjectError(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "NoSuchKey"}}


class ByteBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


def test_runtime_failover_preserves_artifacts_after_recovery(tmp_path: Path) -> None:
    client = FakeS3Client()
    primary = object.__new__(S3ArtifactStore)
    primary.bucket = "test"
    primary._client = client
    backup = FilesystemArtifactStore(tmp_path / "backup")
    store = FailoverArtifactStore(primary, backup)

    original = store.put(b"before outage", media_type="text/plain")
    assert original.storage_backend == "s3"

    client.available = False
    saved = store.put(PAYLOAD, media_type="application/pdf", filename="saved.pdf")
    assert saved.storage_backend == "filesystem"
    assert store.get(saved.artifact_id) == PAYLOAD
    assert store.stat(saved.artifact_id).filename == "saved.pdf"
    assert store.exists(saved.artifact_id)
    with pytest.raises(ProviderUnavailableError):
        store.get(original.artifact_id)
    with pytest.raises(ProviderUnavailableError):
        store.exists(fingerprint_bytes(b"unknown"))

    client.available = True
    assert store.get(original.artifact_id) == b"before outage"
    assert store.get(saved.artifact_id) == PAYLOAD
    assert store.stat(saved.artifact_id).storage_backend == "filesystem"
    with pytest.raises(ArtifactNotFoundError):
        store.get(fingerprint_bytes(b"unknown"))


def test_store_factory_keeps_filesystem_as_runtime_backup(tmp_path: Path) -> None:
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {
                    "id": "minio",
                    "transport": "s3",
                    "endpoint": "http://127.0.0.1:1",
                    "capabilities": ["artifact.store"],
                    "config": {"access_key": "test", "secret_key": "test"},
                }
            ),
            ProviderDescriptor.model_validate(
                {
                    "id": "filesystem",
                    "transport": "in_process",
                    "capabilities": ["artifact.store"],
                    "config": {"root": str(tmp_path / "backup")},
                }
            ),
        ]
    )

    selected = _store_from(providers)

    assert isinstance(selected, FailoverArtifactStore)
    assert isinstance(selected.primary, S3ArtifactStore)
    assert isinstance(selected.fallback, FilesystemArtifactStore)


def test_null_cache_never_reports_a_hit() -> None:
    cache = NullCache()
    cache.put("key", {"value": 1})

    assert cache.get("key") is None
