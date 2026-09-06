"""Statelessness guarantees.

A replica may be replaced at any time. Anything that must survive lives in
external storage or cache, never in process memory.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.documents import FakeDocument

from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import (
    ProviderDescriptor,
    ProviderHealth,
    ProviderRegistry,
)
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

pytestmark = pytest.mark.statelessness

PDF = "application/pdf"
PAYLOAD = b"%PDF-test"

MANIFEST = {
    "id": "document.structure.extract",
    "version": 1,
    "description": "Extract document structure.",
    "input": {"schema": "artifact/document@1", "media_types": [PDF]},
    "output": {"schema": "artifact/structured-document@1"},
}


class CountingProvider:
    """Counts provider invocations so cache hits are observable."""

    calls = 0

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        type(self).calls += 1
        timestamp = datetime(2026, 9, 6, tzinfo=UTC)
        return ExtractionResult(
            document=FakeDocument(),
            backend="docling",
            started_at=timestamp,
            completed_at=timestamp,
            duration_ms=5,
            version="1.32.0",
            configuration={},
        )

    def versions(self) -> dict[str, str]:
        return {"provider": "1.32.0", "docling": "2.124.0"}

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id, healthy=True, checked_at=datetime.now(UTC)
        )


class SharedCache:
    """Stands in for Valkey: state outside any single replica."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.entries[key] = value


def replica(store: FilesystemArtifactStore, cache: SharedCache) -> CapabilityExecutor:
    """Build a fresh executor, as a newly scheduled replica would."""
    descriptor = ProviderDescriptor.model_validate(
        {"id": "docling", "capabilities": ["document.structure.extract"]}
    )
    return CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        CountingProvider,
        cache=cache,
        store=store,
    )


def run(executor: CapabilityExecutor):
    return executor.execute(
        "document.structure.extract",
        PAYLOAD,
        filename="sample.pdf",
        media_type=PDF,
    )


@pytest.fixture(autouse=True)
def reset_call_count() -> None:
    CountingProvider.calls = 0


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "objects")


def test_a_replacement_replica_serves_the_cached_result(
    store: FilesystemArtifactStore,
) -> None:
    cache = SharedCache()

    first = run(replica(store, cache))
    assert CountingProvider.calls == 1
    assert first.provenance.cache_hit is False

    # The first replica is gone; a new one answers the same request.
    second = run(replica(store, cache))

    assert CountingProvider.calls == 1, "cache hit must not reach the provider"
    assert second.provenance.cache_hit is True
    assert second.document == first.document


def test_artifacts_written_by_one_replica_are_readable_by_another(
    store: FilesystemArtifactStore,
) -> None:
    ref = replica(store, SharedCache()).store_artifact(
        PAYLOAD, media_type=PDF, filename="sample.pdf"
    )

    payload, metadata = replica(store, SharedCache()).retrieve_artifact(ref.artifact_id)

    assert payload == PAYLOAD
    assert metadata.artifact_id == ref.artifact_id


def test_executor_holds_no_result_state_between_runs(
    store: FilesystemArtifactStore,
) -> None:
    """Without a shared cache each replica must do the work itself."""
    run(replica(store, SharedCache()))
    run(replica(store, SharedCache()))

    assert CountingProvider.calls == 2


def test_cache_survives_replica_replacement_by_key_not_by_instance(
    store: FilesystemArtifactStore,
) -> None:
    cache = SharedCache()
    key = run(replica(store, cache)).provenance.cache_key

    assert key in cache.entries
    assert list(cache.entries) == [key]


def test_execution_output_is_persisted_outside_the_process(
    store: FilesystemArtifactStore,
) -> None:
    result = run(replica(store, SharedCache()))
    [artifact] = result.artifacts

    assert artifact.storage_backend == "filesystem"
    assert store.exists(artifact.artifact_id)
    assert result.provenance.output_fingerprints == [artifact.artifact_id]
