"""Resync must exhaust pagination and fetch fresh remote content."""
from unittest.mock import Mock

import pytest

from acessilia_toolbox.core.dataset import DatasetArtifactRef, DatasetItem, ItemSummary
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.dataset import DatasetAdapter
from acessilia_toolbox.providers.storage import FilesystemArtifactStore


@pytest.mark.parametrize('total,page_cap', [(5, 2), (10001, 10000)])
def test_sync_fetches_all_pages(tmp_path, total, page_cap):
    provider = Mock()
    descriptor = ProviderDescriptor(id='dataset-test', version='1', transport='in_process',
                                    capabilities=['dataset.sync'], config={'mirror': True})
    def listing(dataset_id, split, revision, limit, offset):
        return [ItemSummary(id=str(i), dataset=dataset_id, split=split)
                for i in range(offset, min(offset + min(limit, page_cap), total))]
    provider.list_items.side_effect = listing
    provider.get_item.side_effect = lambda dataset, item, split, revision: DatasetItem(
        id=item, dataset=dataset, split=split)
    adapter = DatasetAdapter(descriptor, provider, store=FilesystemArtifactStore(tmp_path))
    adapter._sync('dataset', split='input')
    assert provider.get_item.call_count == total


@pytest.mark.parametrize("collection", ["artifacts", "annotations"])
def test_sync_refreshes_previously_mirrored_artifacts(tmp_path, collection):
    provider = Mock()
    descriptor = ProviderDescriptor(id='dataset-test', version='1', transport='in_process',
                                    capabilities=['dataset.sync'], config={'mirror': True})
    provider.list_items.side_effect = lambda dataset, split, revision, limit, offset: (
        [ItemSummary(id='one', dataset=dataset, split=split)] if offset == 0 else [])
    provider.get_item.return_value = DatasetItem(id='one', dataset='dataset', split='input',
        **{collection: [DatasetArtifactRef(path='one.txt', media_type='text/plain')]})
    provider.get_artifact.side_effect = [(b'old', 'text/plain'), (b'new', 'text/plain')]
    adapter = DatasetAdapter(descriptor, provider, store=FilesystemArtifactStore(tmp_path))
    assert adapter._get_artifact_maybe_mirrored('dataset', 'one.txt')[0] == b'old'
    assert adapter._sync('dataset', split='input')[0]['status'] == 'mirrored'
    assert adapter._get_artifact_maybe_mirrored('dataset', 'one.txt')[0] == b'new'
    assert provider.get_artifact.call_count == 2


def test_sync_rejects_provider_that_ignores_offset(tmp_path):
    from acessilia_toolbox.core.errors import DatasetProviderError

    provider = Mock()
    descriptor = ProviderDescriptor(
        id="dataset-test", version="1", transport="in_process",
        capabilities=["dataset.sync"], config={"mirror": True},
    )
    provider.list_items.return_value = [ItemSummary(id="one", dataset="ds", split="input")]
    provider.get_item.return_value = DatasetItem(id="one", dataset="ds", split="input")
    adapter = DatasetAdapter(descriptor, provider, store=FilesystemArtifactStore(tmp_path))
    with pytest.raises(DatasetProviderError, match="pagination repeated"):
        adapter._sync("ds", split="input")
    assert provider.list_items.call_count == 2


def test_shared_annotation_is_refreshed_once_per_sync(tmp_path):
    provider = Mock()
    descriptor = ProviderDescriptor(
        id="dataset-test", version="1", transport="in_process",
        capabilities=["dataset.sync"], config={"mirror": True},
    )
    provider.list_items.side_effect = lambda dataset, split, revision, limit, offset: (
        [ItemSummary(id=str(i), dataset=dataset, split=split) for i in range(2)]
        if offset == 0 else []
    )
    provider.get_item.side_effect = lambda dataset, item, split, revision: DatasetItem(
        id=item, dataset=dataset, split=split,
        annotations=[DatasetArtifactRef(path="shared.csv", media_type="text/csv")],
    )
    provider.get_artifact.return_value = (b"fresh", "text/csv")
    adapter = DatasetAdapter(descriptor, provider, store=FilesystemArtifactStore(tmp_path))
    assert len(adapter._sync("ds", split="input")) == 2
    provider.get_artifact.assert_called_once_with("ds", "shared.csv", None)
    adapter._sync("ds", split="input")
    assert provider.get_artifact.call_count == 2
