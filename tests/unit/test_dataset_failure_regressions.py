"""Remote failures must not be reported as empty datasets."""

import httpx
import pytest

from acessilia_toolbox.core.errors import DatasetProviderError
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.dataset_github import GitHubDatasetProvider
from acessilia_toolbox.providers.dataset_huggingface import HuggingFaceDatasetProvider


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
@pytest.mark.parametrize("source", ["github", "huggingface"])
def test_remote_dataset_failure_is_not_an_empty_listing(status, source):
    descriptor = ProviderDescriptor(
        id="dataset-test", version="1", transport="in_process", capabilities=["dataset.list_items"]
    )
    cls = GitHubDatasetProvider if source == "github" else HuggingFaceDatasetProvider
    provider = cls(descriptor)
    provider._http.close()
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        provider._http = client
        with pytest.raises(DatasetProviderError):
            if source == "github":
                provider.list_items("acessilia-dataset", "input")
            else:
                provider._list_dir("org/repo", "main", "images")


@pytest.mark.parametrize("content", [b"not json", b'{"error": "bad response"}', b"null"])
def test_huggingface_rejects_malformed_directory_response(content):
    descriptor = ProviderDescriptor(
        id="dataset-test", version="1", transport="in_process", capabilities=["dataset.list_items"]
    )
    provider = HuggingFaceDatasetProvider(descriptor)
    provider._http.close()
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    ) as client:
        provider._http = client
        with pytest.raises(DatasetProviderError):
            provider._list_dir("org/repo", "main", "images")
