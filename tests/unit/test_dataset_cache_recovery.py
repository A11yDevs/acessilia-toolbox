"""Stale dataset caches cannot replace or prevent valid provider results."""

from time import time
from unittest.mock import Mock

import pytest
from tests.unit.test_dataset_providers import StubDatasetProvider

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.dataset import DatasetAdapter
from acessilia_toolbox.providers.storage import FilesystemArtifactStore


class Cache:
    def __init__(self, value):
        self.value = value

    def get(self, key):
        return self.value

    def put(self, key, value):
        self.value = value


@pytest.mark.parametrize(
    "operation,method",
    [
        ("dataset.list", "list_datasets"),
        ("dataset.describe", "describe"),
        ("dataset.list_splits", "list_splits"),
        ("dataset.list_items", "list_items"),
    ],
)
@pytest.mark.parametrize(
    "cached", [{"stale": True}, {"data": None}, {"data": 42}, {"data": [{"invalid": True}]}]
)
def test_stale_metadata_is_refetched_then_cached(operation, method, cached):
    descriptor = ProviderDescriptor(id="dataset-test", capabilities=[operation])
    provider = Mock(wraps=StubDatasetProvider(descriptor))
    adapter = DatasetAdapter(descriptor, provider, cache=Cache({**cached, "cached_at": time()}))
    params = {"dataset_id": "stub-ds", "split": "input"}
    first = adapter.execute(operation, b"{}", parameters=params)
    second = adapter.execute(operation, b"{}", parameters=params)
    assert first.document == second.document
    assert first.document
    assert getattr(provider, method).call_count == 1


@pytest.mark.parametrize(
    "cached",
    [{"stale": True}, {"artifact_id": "invalid", "size": 1}, {"artifact_id": 42, "size": 1}],
)
def test_invalid_mirror_reference_is_recovered(tmp_path, cached):
    descriptor = ProviderDescriptor(
        id="dataset-test", capabilities=["dataset.get_artifact"], config={"mirror": True}
    )
    provider = Mock(wraps=StubDatasetProvider(descriptor))
    adapter = DatasetAdapter(
        descriptor,
        provider,
        cache=Cache({"ref": cached, "cached_at": time()}),
        store=FilesystemArtifactStore(tmp_path),
    )
    first = adapter._get_artifact_maybe_mirrored("stub-ds", "one.txt")
    second = adapter._get_artifact_maybe_mirrored("stub-ds", "one.txt")
    assert first == second == (b"fake-content", "text/plain")
    provider.get_artifact.assert_called_once()


def test_metadata_expiry_fetches_current_provider_state(monkeypatch):
    from acessilia_toolbox.providers import dataset as module

    clock = [1000.0]
    monkeypatch.setattr(module, "time", lambda: clock[0])
    descriptor = ProviderDescriptor(id="dataset-test", capabilities=["dataset.list"])
    provider = Mock(wraps=StubDatasetProvider(descriptor))
    adapter = DatasetAdapter(descriptor, provider, cache=Cache(None))
    first = adapter.execute("dataset.list", b"{}").document
    clock[0] += module._META_TTL + 1
    replacement = (
        StubDatasetProvider(descriptor)
        .list_datasets()[0]
        .model_copy(update={"name": "Updated dataset"})
    )
    provider.list_datasets.return_value = [replacement]
    second = adapter.execute("dataset.list", b"{}").document
    assert first[0]["name"] != second[0]["name"] == "Updated dataset"
    assert provider.list_datasets.call_count == 2
    assert adapter.execute("dataset.list", b"{}").document == second
    assert provider.list_datasets.call_count == 2


@pytest.mark.parametrize("recorded", [None, "invalid", True, float("nan"), float("inf")])
def test_invalid_metadata_timestamp_is_a_cache_miss(recorded):
    descriptor = ProviderDescriptor(id="dataset-test", capabilities=["dataset.list"])
    provider = Mock(wraps=StubDatasetProvider(descriptor))
    cache = Cache({"cached_at": recorded, "data": []})
    adapter = DatasetAdapter(descriptor, provider, cache=cache)
    assert adapter.execute("dataset.list", b"{}").document
    provider.list_datasets.assert_called_once()


@pytest.mark.parametrize("use_cache", [True, False])
def test_mirrored_reference_expiry_fetches_fresh_content(tmp_path, monkeypatch, use_cache):
    from acessilia_toolbox.providers import dataset as module

    clock = [1000.0]
    monkeypatch.setattr(module, "time", lambda: clock[0])
    descriptor = ProviderDescriptor(
        id="dataset-test", capabilities=["dataset.get_artifact"], config={"mirror": True}
    )
    provider = Mock(wraps=StubDatasetProvider(descriptor))
    provider.get_artifact.side_effect = [(b"old", "text/plain"), (b"new", "text/plain")]
    adapter = DatasetAdapter(
        descriptor,
        provider,
        cache=Cache(None) if use_cache else None,
        store=FilesystemArtifactStore(tmp_path),
    )
    assert adapter._get_artifact_maybe_mirrored("stub-ds", "one.txt")[0] == b"old"
    assert adapter._get_artifact_maybe_mirrored("stub-ds", "one.txt")[0] == b"old"
    clock[0] += module._ARTIFACT_TTL + 1
    assert adapter._get_artifact_maybe_mirrored("stub-ds", "one.txt")[0] == b"new"
    assert provider.get_artifact.call_count == 2


@pytest.mark.parametrize(
    "operation", ["list", "describe", "list_splits", "list_items", "get_artifact"]
)
def test_dataset_owned_cache_is_not_shadowed_by_execution_cache(operation):
    from pathlib import Path

    from acessilia_toolbox.core.capability import CapabilityRegistry

    registry = CapabilityRegistry.from_directory(Path("capabilities"))
    assert not registry.get(f"dataset.{operation}").execution.cacheable


def test_executor_respects_dataset_metadata_expiry(monkeypatch):
    from pathlib import Path

    from acessilia_toolbox.core.capability import CapabilityRegistry
    from acessilia_toolbox.core.executor import CapabilityExecutor
    from acessilia_toolbox.core.provider import ProviderRegistry
    from acessilia_toolbox.providers import dataset as module

    clock = [1000.0]
    monkeypatch.setattr(module, "time", lambda: clock[0])
    descriptor = ProviderDescriptor(id="dataset-github", capabilities=["dataset.list"])
    provider = Mock(wraps=StubDatasetProvider(descriptor))
    cache = Cache(None)
    adapter = DatasetAdapter(descriptor, provider, cache=cache)
    executor = CapabilityExecutor(
        CapabilityRegistry.from_directory(Path("capabilities")),
        ProviderRegistry([descriptor]),
        lambda _: adapter,
        cache=cache,
    )
    executor.execute("dataset.list", b"{}", filename="query.json", media_type="application/json")
    executor.execute("dataset.list", b"{}", filename="query.json", media_type="application/json")
    assert provider.list_datasets.call_count == 1
    clock[0] += module._META_TTL + 1
    executor.execute("dataset.list", b"{}", filename="query.json", media_type="application/json")
    assert provider.list_datasets.call_count == 2
