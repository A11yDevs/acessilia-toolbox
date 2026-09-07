"""Contract conformance for artifact.store and artifact.retrieve.

Every S3/MinIO provider bound to these capabilities must satisfy these
assertions. The suite skips gracefully when the provider is unreachable,
so it can run in both CI (with containers) and local dev (without).
"""

from __future__ import annotations

import os

import pytest

from acessilia_toolbox.core.artifact import ArtifactRef
from acessilia_toolbox.core.errors import ArtifactNotFoundError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.storage import S3ArtifactStore, create_artifact_store

pytestmark = pytest.mark.contract

PAYLOAD = b"%PDF-contract-test"
BUCKET_CHECK = b"bucket-health-check"


def _build_descriptor(**overrides: str) -> ProviderDescriptor:
    base = {
        "id": "minio",
        "version": "2025.04",
        "transport": "s3",
        "endpoint": os.getenv("MINIO_URL", "http://localhost:9000"),
        "capabilities": ["artifact.store", "artifact.retrieve"],
        "config": {
            "bucket": "acessilia",
            "access_key": os.getenv("MINIO_ACCESS_KEY", "acessiliaadmin"),
            "secret_key": os.getenv("MINIO_SECRET_KEY", "sMvhTvcQWfZry72Zz7lDuVL7D5NUsQK"),
        },
    }
    for k, v in overrides.items():
        if "." in k:
            section, key = k.split(".", 1)
            base[section][key] = v  # type: ignore[index]
        else:
            base[k] = v  # type: ignore[assignment]
    return ProviderDescriptor.model_validate(base)


@pytest.fixture(scope="session")
def store_descriptor() -> ProviderDescriptor:
    return _build_descriptor()


@pytest.fixture(scope="session")
def live_store(store_descriptor: ProviderDescriptor) -> S3ArtifactStore | None:
    """Return a connected store or skip if MinIO is unreachable."""
    store = create_artifact_store(store_descriptor)
    if not isinstance(store, S3ArtifactStore):
        pytest.skip("factory did not return an S3 store")

    try:
        store._client.list_buckets()
    except Exception as exc:
        pytest.skip(
            f"MinIO unreachable at {store_descriptor.endpoint}: {exc}. "
            "Start with `docker compose up -d`."
        )
    return store


# ── Health / availability ──────────────────────────────────────────────


def test_minio_is_reachable(live_store: S3ArtifactStore | None) -> None:
    """The provider must be online for contract tests to proceed."""
    assert live_store is not None


# ── Content-addressable identity ───────────────────────────────────────


def test_identity_comes_from_content(live_store: S3ArtifactStore) -> None:
    first = live_store.put(PAYLOAD, filename="a.pdf")
    second = live_store.put(PAYLOAD, filename="b.pdf")

    assert first.artifact_id == second.artifact_id == fingerprint_bytes(PAYLOAD)


def test_different_content_yields_different_id(live_store: S3ArtifactStore) -> None:
    assert live_store.put(PAYLOAD).artifact_id != live_store.put(b"other").artifact_id


# ── Round-trip ─────────────────────────────────────────────────────────


def test_stored_content_round_trips(live_store: S3ArtifactStore) -> None:
    ref = live_store.put(PAYLOAD, media_type="application/pdf", filename="report.pdf")

    retrieved = live_store.get(ref.artifact_id)

    assert retrieved == PAYLOAD
    assert live_store.exists(ref.artifact_id)


def test_metadata_survives_retrieval(live_store: S3ArtifactStore) -> None:
    ref = live_store.put(PAYLOAD, media_type="application/pdf", filename="report.pdf")
    stat = live_store.stat(ref.artifact_id)

    assert stat.media_type == "application/pdf"
    assert stat.filename == "report.pdf"
    assert stat.size == len(PAYLOAD)
    assert stat.storage_backend == "s3"


def test_stat_returns_uri(live_store: S3ArtifactStore) -> None:
    ref = live_store.put(PAYLOAD)
    stat = live_store.stat(ref.artifact_id)

    assert stat.uri.startswith("s3://")
    assert stat.artifact_id == ref.artifact_id


# ── Idempotency ────────────────────────────────────────────────────────


def test_storing_identical_content_is_idempotent(live_store: S3ArtifactStore) -> None:
    ref = live_store.put(PAYLOAD)
    second_ref = live_store.put(PAYLOAD)

    assert ref.artifact_id == second_ref.artifact_id
    assert live_store.get(ref.artifact_id) == PAYLOAD


# ── Errors ─────────────────────────────────────────────────────────────


def test_unknown_artifact_is_reported(live_store: S3ArtifactStore) -> None:
    missing = fingerprint_bytes(b"never-stored")

    assert not live_store.exists(missing)
    with pytest.raises(ArtifactNotFoundError):
        live_store.get(missing)
    with pytest.raises(ArtifactNotFoundError):
        live_store.stat(missing)


# ── Cross-backend contract ─────────────────────────────────────────────


def test_ref_is_portable_across_backends(live_store: S3ArtifactStore) -> None:
    """An ArtifactRef from S3 has the same shape as one from filesystem."""
    ref = live_store.put(PAYLOAD)

    assert isinstance(ref, ArtifactRef)
    assert ref.artifact_id.startswith("sha256:")
    assert ref.size == len(PAYLOAD)
    assert ref.storage_backend == "s3"
