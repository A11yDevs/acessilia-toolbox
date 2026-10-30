"""Dataset filenames remain data when used in raw-content URLs."""

import httpx
import pytest

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.dataset_github import GitHubDatasetProvider


@pytest.mark.parametrize(
    "path",
    [
        "input/plain.pdf",
        "input/lesson#1.pdf",
        "input/lesson?1.pdf",
        "input/literal%2F.pdf",
        "input/a b.pdf",
        "input/álgebra.pdf",
    ],
)
def test_github_artifact_path_is_encoded_without_changing_filename(path):
    provider = GitHubDatasetProvider(
        ProviderDescriptor(id="dataset-github", capabilities=["dataset.get_artifact"])
    )
    provider._http.close()
    seen = []

    def serve(request):
        seen.append(request.url)
        return httpx.Response(200, content=b"fixture")

    provider._http = httpx.Client(transport=httpx.MockTransport(serve))
    try:
        assert provider.get_artifact("acessilia-dataset", path)[0] == b"fixture"
    finally:
        provider._http.close()
    assert seen[0].path == "/A11yDevs/acessilia-dataset/main/" + path
    assert not seen[0].query
    assert not seen[0].fragment
