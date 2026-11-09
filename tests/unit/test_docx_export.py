"""document.export.docx: the pandoc-docx adapter and the sidecar's /render/docx route."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError, ProviderExecutionError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pandoc_docx import DOCX_MEDIA_TYPE, PandocDocxProvider
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = PROJECT_ROOT / "docker" / "pdf-ua-render" / "server.py"
CAPABILITY = "document.export.docx"
DOCX_BYTES = b"PK\x03\x04 a docx archive"
AST = {
    "pandoc-api-version": [1, 23, 1],
    "meta": {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "Relatório"}]}},
    "blocks": [{"t": "Para", "c": [{"t": "Str", "c": "Olá"}]}],
}
AST_BYTES = json.dumps(AST).encode("utf-8")
VERSIONS = {
    "version": "1.1.0",
    "pandoc": "3.9.0.2",
    "engine": "lualatex",
    "engine_version": "This is LuaHBTeX, Version 1.22.0 (TeX Live 2025)",
    "latex_kernel": "2025-11-01 pl0",
    "template_sha256": "ab" * 32,
}


def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "pandoc-docx",
            "version": "1.0",
            "transport": "http",
            "endpoint": "http://pdf-ua-render:5007",
            "timeout_seconds": 5.0,
            "capabilities": [CAPABILITY],
            "media_types": ["application/json"],
        }
    )


def provider_with(
    render: Callable[[httpx.Request], httpx.Response] | None = None,
    versions: dict[str, str] = VERSIONS,
) -> tuple[PandocDocxProvider, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json=versions)
        if request.url.path == "/render/docx":
            if render is not None:
                return render(request)
            return httpx.Response(200, content=DOCX_BYTES)
        return httpx.Response(404)

    adapter = PandocDocxProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter, calls


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value


def executor_with(
    adapter: PandocDocxProvider,
    store: FilesystemArtifactStore,
    cache: MemoryCache | None = None,
) -> CapabilityExecutor:
    return CapabilityExecutor(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"),
        ProviderRegistry([adapter.descriptor]),
        lambda _: adapter,
        store=store,
        cache=cache,
    )


def export(executor: CapabilityExecutor):
    return executor.execute(
        CAPABILITY,
        AST_BYTES,
        filename="relatorio.json",
        media_type="application/json",
        parameters={"lang": "pt-BR"},
    )


# ── Adapter ─────────────────────────────────────────────────────────────


def test_factory_creates_the_adapter() -> None:
    assert isinstance(create_adapter(descriptor()), PandocDocxProvider)


def test_adapter_returns_the_docx_as_a_binary_output() -> None:
    adapter, calls = provider_with()

    result = adapter.execute(
        CAPABILITY, AST_BYTES, filename="relatorio.json", media_type="application/json"
    )

    [output] = result.outputs
    assert (output.payload, output.media_type, output.filename) == (
        DOCX_BYTES,
        DOCX_MEDIA_TYPE,
        "relatorio.docx",
    )
    assert result.document == {
        "artifact_id": fingerprint_bytes(DOCX_BYTES),
        "media_type": DOCX_MEDIA_TYPE,
        "size_bytes": len(DOCX_BYTES),
        "toolchain": {"version": "1.1.0", "pandoc": "3.9.0.2"},
    }
    [render] = [call for call in calls if call.url.path == "/render/docx"]
    assert b'name="lang"' in render.content and b"pt-BR" in render.content


def test_only_pandoc_and_the_sidecar_version_shape_the_docx() -> None:
    adapter, _ = provider_with()
    assert adapter.versions() == {"provider": "1.1.0", "version": "1.1.0", "pandoc": "3.9.0.2"}


def test_rejected_ast_is_an_input_error() -> None:
    adapter, _ = provider_with(lambda _: httpx.Response(422, json={"detail": "raw OpenXML"}))
    with pytest.raises(InvalidInputError, match="DOCX renderer rejected the AST: raw OpenXML"):
        adapter.execute(CAPABILITY, AST_BYTES, filename="a.json", media_type="application/json")


def test_a_body_that_is_not_a_zip_is_an_execution_error() -> None:
    adapter, _ = provider_with(lambda _: httpx.Response(200, content=b"%PDF-1.7"))
    with pytest.raises(ProviderExecutionError, match="not a DOCX file"):
        adapter.execute(CAPABILITY, AST_BYTES, filename="a.json", media_type="application/json")


# ── Executor ────────────────────────────────────────────────────────────


def test_docx_is_stored_and_returned_by_reference(tmp_path: Path) -> None:
    adapter, _ = provider_with()
    store = FilesystemArtifactStore(tmp_path)

    result = export(executor_with(adapter, store))

    assert result.provenance.capability == CAPABILITY
    [docx_ref] = [ref for ref in result.artifacts if ref.media_type == DOCX_MEDIA_TYPE]
    assert docx_ref.artifact_id == result.document["artifact_id"]
    assert store.get(docx_ref.artifact_id) == DOCX_BYTES


def test_a_tex_upgrade_does_not_change_the_docx_cache_key(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    before = export(executor_with(provider_with()[0], store)).provenance.cache_key
    newer_tex = {**VERSIONS, "latex_kernel": "2026-06-01 pl0", "template_sha256": "cd" * 32}

    after = export(executor_with(provider_with(versions=newer_tex)[0], store))

    assert after.provenance.cache_key == before


@pytest.mark.parametrize(
    "change", [{"version": "1.2.0"}, {"pandoc": "3.10"}], ids=["sidecar", "pandoc"]
)
def test_a_sidecar_or_pandoc_upgrade_changes_the_docx_cache_key(
    tmp_path: Path, change: dict[str, str]
) -> None:
    store = FilesystemArtifactStore(tmp_path)
    cache = MemoryCache()
    before = export(executor_with(provider_with()[0], store, cache)).provenance.cache_key

    after = export(executor_with(provider_with(versions={**VERSIONS, **change})[0], store, cache))

    assert after.provenance.cache_key != before
    assert not after.provenance.cache_hit


def test_cache_hit_reuses_the_stored_docx_without_rendering(tmp_path: Path) -> None:
    adapter, calls = provider_with()
    cache = MemoryCache()
    executor = executor_with(adapter, FilesystemArtifactStore(tmp_path), cache)

    first = export(executor)
    second = export(executor)

    assert second.provenance.cache_hit
    assert second.document["artifact_id"] == first.document["artifact_id"]
    assert sum(call.url.path == "/render/docx" for call in calls) == 1
    # Neither the response document nor the cached result embeds the bytes.
    assert "PK" not in json.dumps(second.model_dump(mode="json"))
    assert "PK" not in json.dumps(next(iter(cache.data.values())))


# ── Sidecar route ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def server() -> Any:
    spec = importlib.util.spec_from_file_location("pdf_ua_render_server_docx", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(server: Any) -> TestClient:
    return TestClient(server.app)


def post_ast(client: TestClient, body: bytes, **data: str) -> httpx.Response:
    return client.post(
        "/render/docx", files={"file": ("ast.json", body, "application/json")}, data=data
    )


def test_server_version_reports_its_capabilities(client: TestClient) -> None:
    payload = client.get("/version").json()
    assert payload["capability"] == "document.export.pdf_ua"
    assert payload["capabilities"] == ["document.export.pdf_ua", "document.export.docx"]
    assert {"version", "pandoc"} <= payload.keys()


@pytest.mark.parametrize(
    "node",
    [
        {"t": "RawBlock", "c": ["openxml", "<w:p><w:r><w:t>injected</w:t></w:r></w:p>"]},
        {"t": "RawInline", "c": ["OpenXML", "<w:r><w:t>injected</w:t></w:r>"]},
    ],
    ids=["block", "inline"],
)
def test_server_rejects_raw_openxml(client: TestClient, node: dict[str, Any]) -> None:
    body = json.dumps({**AST, "blocks": [{"t": "Para", "c": [node]}]}).encode()
    response = post_ast(client, body)
    assert response.status_code == 422
    assert response.json()["detail"] == "raw OpenXML blocks are not accepted"


def test_server_lets_pandoc_drop_raw_tex(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The docx writer drops raw TeX, so it is no reason to refuse the AST.
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        Path(command[command.index("-o") + 1]).write_bytes(DOCX_BYTES)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    node = {"t": "RawInline", "c": ["latex", "\\relax"]}
    body = json.dumps({**AST, "blocks": [{"t": "Para", "c": [node]}]}).encode()
    assert post_ast(client, body).status_code == 200


def test_server_rejects_an_invalid_language_tag(client: TestClient) -> None:
    assert post_ast(client, AST_BYTES, lang="pt-BR}\\evil").status_code == 400


def test_server_runs_pandoc_sandboxed_with_the_language(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        Path(command[command.index("-o") + 1]).write_bytes(DOCX_BYTES)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    response = post_ast(client, AST_BYTES, lang="en-US")

    assert response.status_code == 200
    assert response.content == DOCX_BYTES
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    [command] = commands
    assert "--sandbox" in command
    assert command[command.index("--to") + 1] == "docx"
    assert "lang=en-US" in command


def test_server_reports_pandoc_failures_as_unprocessable(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b"", b"bad AST"),
    )
    response = post_ast(client, AST_BYTES)
    assert response.status_code == 422
    assert "bad AST" in response.json()["detail"]


def test_server_without_pandoc_is_not_ready(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    assert post_ast(client, AST_BYTES).status_code == 503
