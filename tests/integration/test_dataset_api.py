"""Integration tests for dataset REST endpoints.

Uses a StubDatasetProvider + TestClient to verify the HTTP contract
without requiring live GitHub/HuggingFace access.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.dataset import DatasetInfo, DatasetItem, ItemSummary, SplitInfo
from acessilia_toolbox.core.errors import (
    DatasetNotFoundError,
    ItemNotFoundError,
    SplitNotFoundError,
)
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import (
    ProviderDescriptor,
    ProviderHealth,
    ProviderRegistry,
)

pytestmark = pytest.mark.integration

DATASET_CAPABILITIES = [
    "dataset.list", "dataset.describe", "dataset.list_splits",
    "dataset.list_items", "dataset.get_item", "dataset.get_artifact",
    "dataset.sample",
]


class StubDatasetProvider:
    """In-memory stub for dataset integration tests."""

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
            ItemSummary(id=f"item_{i}", dataset=dataset_id, split=split)
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
        return b"fake-content", "text/plain"

    def sample(
        self,
        dataset_id: str,
        split: str,
        n: int = 5,
        revision: str | None = None,
    ) -> Sequence[DatasetItem]:
        return [DatasetItem(id=f"sample_{i}", dataset=dataset_id, split=split) for i in range(n)]


class StubDatasetAdapter:
    """Wraps StubDatasetProvider to conform to ProviderAdapter."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self._provider = StubDatasetProvider(descriptor)

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str = "",
        media_type: str = "",
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        params = dict(parameters or {})
        dataset_id = params.pop("dataset_id", "")
        revision = params.pop("revision", None)
        split = params.pop("split", "")
        item_id = params.pop("item_id", "")
        artifact_path = params.pop("artifact_path", "")
        limit = int(params.pop("limit", 100))
        offset = int(params.pop("offset", 0))
        n = int(params.pop("n", 5))

        started = datetime.now(UTC)

        if capability_id == "dataset.list":
            doc = [d.model_dump(mode="json") for d in self._provider.list_datasets()]
        elif capability_id == "dataset.describe":
            doc = self._provider.describe(dataset_id, revision).model_dump(mode="json")
        elif capability_id == "dataset.list_splits":
            doc = [s.model_dump(mode="json")
                   for s in self._provider.list_splits(dataset_id, revision)]
        elif capability_id == "dataset.list_items":
            doc = [i.model_dump(mode="json")
                   for i in self._provider.list_items(
                       dataset_id, split, revision, limit, offset
                   )]
        elif capability_id == "dataset.get_item":
            doc = self._provider.get_item(
                dataset_id, item_id, split, revision
            ).model_dump(mode="json")
        elif capability_id == "dataset.get_artifact":
            data, mt = self._provider.get_artifact(dataset_id, artifact_path, revision)
            doc = {"payload": data.hex(), "media_type": mt}
        elif capability_id == "dataset.sample":
            doc = [i.model_dump(mode="json")
                   for i in self._provider.sample(dataset_id, split, n, revision)]
        else:
            raise ValueError(f"unknown capability: {capability_id}")

        completed = datetime.now(UTC)
        return ExtractionResult(
            document=doc,
            backend=self.descriptor.id,
            started_at=started,
            completed_at=completed,
            duration_ms=7,
            version=self.descriptor.version,
        )

    def versions(self) -> dict[str, str]:
        return {"provider": self.descriptor.version}

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            version=self.descriptor.version,
            checked_at=datetime.now(UTC),
        )


@pytest.fixture
def client() -> TestClient:
    capabilities = CapabilityRegistry.from_directory(
        Path(__file__).resolve().parents[2] / "capabilities"
    )
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate({
                "id": "dataset-github",
                "version": "1.0",
                "transport": "in_process",
                "capabilities": DATASET_CAPABILITIES,
            })
        ]
    )
    app = create_app(capabilities, providers)
    app.state.executor = CapabilityExecutor(
        capabilities, providers, lambda d: StubDatasetAdapter(d)
    )
    return TestClient(app)


class TestDatasetAPI:
    """Verify dataset REST endpoints."""

    def test_list_datasets(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["id"] == "stub-ds"

    def test_describe_dataset(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/stub-ds")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "stub-ds"

    def test_describe_dataset_not_found(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/nope")
        assert resp.status_code == 404  # DatasetNotFoundError

    def test_list_splits(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/stub-ds/splits")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "input"

    def test_list_items(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/stub-ds/splits/input/items")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 10

    def test_list_items_pagination(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/stub-ds/splits/input/items?limit=3&offset=7")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    def test_get_item(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/stub-ds/splits/input/items/item_0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "item_0"

    def test_get_item_not_found(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/stub-ds/splits/input/items/nope")
        assert resp.status_code == 404

    def test_get_artifact(self, client: TestClient) -> None:
        resp = client.get(
            "/v1/datasets/stub-ds/splits/input/items/item_0/artifacts/input/001.pdf"
        )
        assert resp.status_code == 200
        assert resp.content == b"fake-content"

    def test_sample(self, client: TestClient) -> None:
        resp = client.get("/v1/datasets/stub-ds/splits/input/sample?n=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    def test_health_still_works(self, client: TestClient) -> None:
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
