"""The preparatory pin must be usable before the new Dataset contract exists."""

import subprocess
from pathlib import Path

import httpx

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.dataset import DatasetAdapter
from acessilia_toolbox.providers.dataset_github import DATASET_DEFS, GitHubDatasetProvider

REVISION = "9ed07b28aeddb69721228f2dd66de6f9794c0001"


def test_legacy_fixture_and_provider_use_the_same_published_revision():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "dataset"
    assert DATASET_DEFS["acessilia-dataset"]["default_revision"] == REVISION
    assert subprocess.check_output(
        ["git", "-C", str(fixture), "rev-parse", "HEAD"], text=True
    ).strip() == REVISION
    descriptor = ProviderDescriptor(id="dataset-github", capabilities=["dataset.get_artifact"])
    provider = GitHubDatasetProvider(descriptor)
    provider._http.close()
    seen = []

    def serve(request):
        seen.append(request.url.path)
        return httpx.Response(200, content=b"legacy")

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        provider._http = client
        provider.get_artifact("acessilia-dataset", "input/001.pdf")
        provider.get_artifact("acessilia-dataset", "input/001.pdf", "other-revision")
    assert seen == [
        f"/A11yDevs/acessilia-dataset/{REVISION}/input/001.pdf",
        "/A11yDevs/acessilia-dataset/other-revision/input/001.pdf",
    ]


def test_pin_namespace_invalidates_warmed_metadata_and_mirror_references():
    descriptor = ProviderDescriptor(id="dataset-github", capabilities=["dataset.get_artifact"])
    provider = GitHubDatasetProvider(descriptor)
    adapter = DatasetAdapter(descriptor, provider)
    kw = {"dataset_id": "acessilia-dataset", "revision": None}
    try:
        pinned_meta = adapter._meta_cache_key("list_items", kw)
        pinned_artifact = adapter._store_key("acessilia-dataset", "input/001.pdf")
        provider._defs["acessilia-dataset"] = {
            **provider._defs["acessilia-dataset"], "default_revision": "main",
        }
        assert adapter._meta_cache_key("list_items", kw) != pinned_meta
        assert adapter._store_key("acessilia-dataset", "input/001.pdf") != pinned_artifact
    finally:
        provider._http.close()
