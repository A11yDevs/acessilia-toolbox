"""Content-addressable artifact storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from acessilia_toolbox.core.artifact import ArtifactRef, NullCache, shards
from acessilia_toolbox.core.errors import ArtifactNotFoundError, ConfigurationError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.storage import (
    FilesystemArtifactStore,
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


def test_null_cache_never_reports_a_hit() -> None:
    cache = NullCache()
    cache.put("key", {"value": 1})

    assert cache.get("key") is None
