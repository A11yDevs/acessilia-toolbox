"""The published legacy Dataset must be usable during the pin-only rollout."""

import subprocess
from pathlib import Path

import httpx

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.dataset_github import GitHubDatasetProvider

REVISION = "9ed07b28aeddb69721228f2dd66de6f9794c0001"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "dataset"
DATASET = "acessilia-dataset"


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(FIXTURE), *args])


def test_provider_resolves_every_legacy_input_to_a_file():
    paths = _git("ls-tree", "-r", "--name-only", REVISION, "input").decode().splitlines()
    document_suffixes = {".pdf", ".png", ".jpeg", ".jpg"}
    source_paths = {path for path in paths if Path(path).suffix in document_suffixes}
    prefix = f"/A11yDevs/{DATASET}/{REVISION}/"

    def serve(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path.startswith(prefix)
        path = path.removeprefix(prefix)
        if path not in paths:
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(200, content=_git("show", f"{REVISION}:{path}"))

    descriptor = ProviderDescriptor(id="dataset-github", capabilities=["dataset.get_item"])
    provider = GitHubDatasetProvider(descriptor)
    provider._http.close()
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        provider._http = client
        summaries = provider.list_items(DATASET, "input", revision=REVISION)
        resolved = {}
        for summary in summaries:
            item = provider.get_item(DATASET, summary.id, "input", REVISION)
            assert len(item.artifacts) == 1
            path = item.artifacts[0].path
            content, _ = provider.get_artifact(DATASET, path, REVISION)
            assert content
            resolved[item.id] = path

    assert len(resolved) == len(source_paths) == 36
    assert set(resolved.values()) == source_paths
    assert resolved["009"] == "input/formula-images/simples_limpa.png"
    assert resolved["036"] == "input/math-reference/velocidade-instantanea.pdf"
