"""Unit tests for dataset providers with mocked HTTP."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from acessilia_toolbox.core.dataset import DatasetInfo, DatasetItem, ItemSummary, SplitInfo
from acessilia_toolbox.core.errors import (
    DatasetNotFoundError,
    ItemNotFoundError,
    SplitNotFoundError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.dataset import DatasetAdapter

# ---------------------------------------------------------------------------
# Stub provider for testing the adapter
# ---------------------------------------------------------------------------


class StubDatasetProvider:
    """In-memory stub that returns canned data."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def list_datasets(self) -> Sequence[DatasetInfo]:
        return [
            DatasetInfo(
                id="stub-ds",
                name="Stub Dataset",
                source="https://example.com/stub",
                source_type="github",
                splits=["input"],
            )
        ]

    def describe(self, dataset_id: str, revision: str | None = None) -> DatasetInfo:
        if dataset_id != "stub-ds":
            raise DatasetNotFoundError(f"unknown {dataset_id}", dataset=dataset_id)
        return DatasetInfo(
            id="stub-ds",
            name="Stub Dataset",
            source="https://example.com/stub",
            source_type="github",
            default_revision=revision or "main",
            splits=["input"],
        )

    def list_splits(self, dataset_id: str, revision: str | None = None) -> Sequence[SplitInfo]:
        if dataset_id != "stub-ds":
            raise DatasetNotFoundError(f"unknown {dataset_id}", dataset=dataset_id)
        return [
            SplitInfo(id="input", item_count=10, has_ground_truth=True),
        ]

    def list_items(
        self,
        dataset_id: str,
        split: str,
        revision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ItemSummary]:
        if dataset_id != "stub-ds":
            raise DatasetNotFoundError(f"unknown {dataset_id}", dataset=dataset_id)
        if split != "input":
            raise SplitNotFoundError(f"unknown {split}", dataset=dataset_id, split=split)
        return [
            ItemSummary(
                id=f"item_{i}", dataset=dataset_id, split=split,
                revision=revision or "main",
            )
            for i in range(offset, min(offset + limit, 10))
        ]

    def get_item(
        self,
        dataset_id: str,
        item_id: str,
        split: str,
        revision: str | None = None,
    ) -> DatasetItem:
        if dataset_id != "stub-ds":
            raise DatasetNotFoundError(f"unknown {dataset_id}", dataset=dataset_id)
        if split != "input":
            raise SplitNotFoundError(f"unknown {split}", dataset=dataset_id, split=split)
        if item_id != "item_0":
            raise ItemNotFoundError(
                f"unknown {item_id}", dataset=dataset_id, split=split, item=item_id
            )
        return DatasetItem(
            id="item_0",
            dataset=dataset_id,
            split=split,
            revision=revision or "main",
        )

    def get_artifact(
        self,
        dataset_id: str,
        artifact_path: str,
        revision: str | None = None,
    ) -> tuple[bytes, str]:
        if dataset_id != "stub-ds":
            raise DatasetNotFoundError(f"unknown {dataset_id}", dataset=dataset_id)
        return b"fake-content", "text/plain"

    def sample(
        self,
        dataset_id: str,
        split: str,
        n: int = 5,
        revision: str | None = None,
    ) -> Sequence[DatasetItem]:
        if dataset_id != "stub-ds":
            raise DatasetNotFoundError(f"unknown {dataset_id}", dataset=dataset_id)
        return [DatasetItem(id=f"sample_{i}", dataset=dataset_id, split=split) for i in range(n)]


@pytest.fixture
def adapter() -> DatasetAdapter:
    descriptor = ProviderDescriptor.model_validate({
        "id": "dataset-stub",
        "version": "1.0",
        "transport": "in_process",
        "capabilities": [
            "dataset.list", "dataset.describe", "dataset.list_splits",
            "dataset.list_items", "dataset.get_item", "dataset.get_artifact",
            "dataset.sample",
        ],
    })
    return DatasetAdapter(descriptor, StubDatasetProvider(descriptor))


class TestDatasetAdapter:
    """Verify the adapter dispatches capability IDs to provider methods."""

    def test_list_datasets(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute("dataset.list", b"")
        doc = result.document
        assert isinstance(doc, list)
        assert doc[0]["id"] == "stub-ds"

    def test_describe(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute(
            "dataset.describe", b"", parameters={"dataset_id": "stub-ds"}
        )
        assert result.document["id"] == "stub-ds"

    def test_describe_unknown(self, adapter: DatasetAdapter) -> None:
        from acessilia_toolbox.core.errors import DatasetNotFoundError

        with pytest.raises(DatasetNotFoundError):
            adapter.execute("dataset.describe", b"{}", parameters={"dataset_id": "nope"})

    def test_list_splits(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute(
            "dataset.list_splits", b"", parameters={"dataset_id": "stub-ds"}
        )
        assert len(result.document) == 1
        assert result.document[0]["id"] == "input"

    def test_list_items(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute(
            "dataset.list_items", b"",
            parameters={"dataset_id": "stub-ds", "split": "input", "limit": 5, "offset": 0},
        )
        assert len(result.document) == 5

    def test_list_items_pagination(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute(
            "dataset.list_items", b"",
            parameters={"dataset_id": "stub-ds", "split": "input", "limit": 3, "offset": 7},
        )
        assert len(result.document) == 3

    def test_get_item(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute(
            "dataset.get_item", b"",
            parameters={"dataset_id": "stub-ds", "split": "input", "item_id": "item_0"},
        )
        assert result.document["id"] == "item_0"

    def test_get_item_unknown(self, adapter: DatasetAdapter) -> None:
        from acessilia_toolbox.core.errors import ItemNotFoundError

        with pytest.raises(ItemNotFoundError):
            adapter.execute(
                "dataset.get_item", b"{}",
                parameters={"dataset_id": "stub-ds", "split": "input", "item_id": "nope"},
            )

    def test_get_artifact(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute(
            "dataset.get_artifact", b"",
            parameters={"dataset_id": "stub-ds", "artifact_path": "input/001.pdf"},
        )
        assert "payload" in result.document
        assert result.document["media_type"] == "text/plain"

    def test_sample(self, adapter: DatasetAdapter) -> None:
        result = adapter.execute(
            "dataset.sample", b"",
            parameters={"dataset_id": "stub-ds", "split": "input", "n": 3},
        )
        assert len(result.document) == 3

    def test_unknown_capability(self, adapter: DatasetAdapter) -> None:
        from acessilia_toolbox.core.errors import DatasetProviderError

        with pytest.raises(DatasetProviderError):
            adapter.execute("dataset.nope", b"")

    def test_health(self, adapter: DatasetAdapter) -> None:
        health = adapter.health()
        assert health.healthy

    def test_versions(self, adapter: DatasetAdapter) -> None:
        versions = adapter.versions()
        assert versions["provider"] == "1.0"
