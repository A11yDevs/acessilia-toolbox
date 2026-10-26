"""document.export.pdf_ua: binary outputs, the pandoc-pdf-ua adapter and its sidecar."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import (
    ConfigurationError,
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pandoc_pdf_ua import PandocPdfUaProvider
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = PROJECT_ROOT / "docker" / "pdf-ua-render" / "server.py"
CAPABILITY = "document.export.pdf_ua"
PDF_BYTES = b"%PDF-1.7\n% tagged\n%%EOF\n"
AST = {
    "pandoc-api-version": [1, 23, 1],
    "meta": {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "Relatório"}]}},
    "blocks": [{"t": "Para", "c": [{"t": "Str", "c": "Olá"}]}],
}
AST_BYTES = json.dumps(AST).encode("utf-8")
VERSIONS = {
    "version": "1.0.0",
    "pandoc": "3.9.0.2",
    "engine": "lualatex",
    "engine_version": "This is LuaHBTeX, Version 1.22.0 (TeX Live 2025)",
    "latex_kernel": "2025-11-01 pl0",
    "template_sha256": "ab" * 32,
    "capability": CAPABILITY,
}


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value


def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "pandoc-pdf-ua",
            "version": "1.0",
            "transport": "http",
            "endpoint": "http://pdf-ua-render:5007",
            "timeout_seconds": 5.0,
            "capabilities": [CAPABILITY],
            "media_types": ["application/json"],
        }
    )


def sidecar(
    render: Callable[[httpx.Request], httpx.Response] | None = None,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json=VERSIONS)
        if request.url.path == "/render":
            if render is not None:
                return render(request)
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(200, json={"status": "ok", "version": "1.0.0"})

    return handler, calls


def provider_with(handler: Callable[[httpx.Request], httpx.Response]) -> PandocPdfUaProvider:
    adapter = PandocPdfUaProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def executor_with(
    adapter: PandocPdfUaProvider,
    *,
    store: FilesystemArtifactStore | None,
    cache: MemoryCache | None = None,
) -> CapabilityExecutor:
    return CapabilityExecutor(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"),
        ProviderRegistry([adapter.descriptor]),
        lambda _: adapter,
        cache=cache,
        store=store,
    )


def export(executor: CapabilityExecutor, **overrides: Any):
    return executor.execute(
        CAPABILITY,
        AST_BYTES,
        filename="relatorio.json",
        media_type="application/json",
        parameters={"lang": "pt-BR"},
        **overrides,
    )


# ── Adapter ─────────────────────────────────────────────────────────────


def test_factory_creates_the_adapter() -> None:
    assert isinstance(create_adapter(descriptor()), PandocPdfUaProvider)


def test_adapter_returns_the_pdf_as_a_binary_output() -> None:
    handler, calls = sidecar()
    result = provider_with(handler).execute(
        CAPABILITY,
        AST_BYTES,
        filename="relatorio.json",
        media_type="application/json",
        parameters={"lang": "en-US"},
    )

    (output,) = result.outputs
    assert output.payload == PDF_BYTES
    assert output.media_type == "application/pdf"
    assert output.filename == "relatorio.pdf"
    assert result.document["artifact_id"] == fingerprint_bytes(PDF_BYTES)
    assert result.document["size_bytes"] == len(PDF_BYTES)
    assert result.document["toolchain"]["latex_kernel"] == "2025-11-01 pl0"
    # The capability field is metadata, not output identity.
    assert "capability" not in result.document["toolchain"]
    render_call = next(call for call in calls if call.url.path == "/render")
    assert b'name="lang"\r\n\r\nen-US' in render_call.content


def test_versions_cover_every_component_that_shapes_the_pdf() -> None:
    handler, _ = sidecar()
    versions = provider_with(handler).versions()
    assert versions["provider"] == "1.0.0"
    for key in ("pandoc", "engine_version", "latex_kernel", "template_sha256"):
        assert versions[key] == VERSIONS[key]


@pytest.mark.parametrize("status", [400, 413, 422])
def test_rejected_ast_is_an_input_error(status: int) -> None:
    handler, _ = sidecar(lambda _: httpx.Response(status, json={"detail": "bad AST"}))
    with pytest.raises(InvalidInputError, match="bad AST"):
        provider_with(handler).execute(
            CAPABILITY, AST_BYTES, filename="a.json", media_type="application/json"
        )


def test_unready_sidecar_is_unavailable() -> None:
    handler, _ = sidecar(lambda _: httpx.Response(503, json={"detail": "missing: lualatex"}))
    with pytest.raises(ProviderUnavailableError):
        provider_with(handler).execute(
            CAPABILITY, AST_BYTES, filename="a.json", media_type="application/json"
        )


def test_sidecar_render_timeout_is_a_timeout() -> None:
    handler, _ = sidecar(lambda _: httpx.Response(504, json={"detail": "exceeded 150s"}))
    with pytest.raises(ProviderTimeoutError, match="exceeded 150s"):
        provider_with(handler).execute(
            CAPABILITY, AST_BYTES, filename="a.json", media_type="application/json"
        )


def test_non_pdf_body_is_an_execution_error() -> None:
    handler, _ = sidecar(lambda _: httpx.Response(200, content=b"<html>proxy</html>"))
    with pytest.raises(ProviderExecutionError, match="not a PDF"):
        provider_with(handler).execute(
            CAPABILITY, AST_BYTES, filename="a.json", media_type="application/json"
        )


def test_unreachable_sidecar_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderUnavailableError):
        provider_with(handler).execute(
            CAPABILITY, AST_BYTES, filename="a.json", media_type="application/json"
        )


# ── Executor: binary outputs ────────────────────────────────────────────


def test_pdf_is_stored_and_returned_by_reference(tmp_path: Path) -> None:
    handler, _ = sidecar()
    store = FilesystemArtifactStore(tmp_path)
    result = export(executor_with(provider_with(handler), store=store))

    pdf_refs = [ref for ref in result.artifacts if ref.media_type == "application/pdf"]
    assert len(pdf_refs) == 1
    assert pdf_refs[0].artifact_id == result.document["artifact_id"]
    assert store.get(pdf_refs[0].artifact_id) == PDF_BYTES
    assert pdf_refs[0].artifact_id in result.provenance.output_fingerprints
    # Neither the response document nor the cached result embeds the bytes.
    assert "%PDF" not in json.dumps(result.model_dump(mode="json"))


def test_cache_hit_reuses_the_stored_pdf_without_rendering(tmp_path: Path) -> None:
    handler, calls = sidecar()
    cache = MemoryCache()
    executor = executor_with(
        provider_with(handler), store=FilesystemArtifactStore(tmp_path), cache=cache
    )

    first = export(executor)
    renders = sum(call.url.path == "/render" for call in calls)
    second = export(executor)

    assert second.provenance.cache_hit
    assert second.document["artifact_id"] == first.document["artifact_id"]
    assert sum(call.url.path == "/render" for call in calls) == renders
    assert "%PDF" not in json.dumps(next(iter(cache.data.values())))


def test_cache_hit_with_a_deleted_pdf_renders_again(tmp_path: Path) -> None:
    handler, calls = sidecar()
    store = FilesystemArtifactStore(tmp_path)
    executor = executor_with(provider_with(handler), store=store, cache=MemoryCache())

    first = export(executor)
    for stored in tmp_path.rglob("*"):
        if stored.is_file():
            stored.unlink()
    second = export(executor)

    assert not second.provenance.cache_hit
    assert sum(call.url.path == "/render" for call in calls) == 2
    assert store.get(second.document["artifact_id"]) == PDF_BYTES
    assert second.document["artifact_id"] == first.document["artifact_id"]


def test_binary_outputs_require_an_artifact_store_before_rendering() -> None:
    handler, calls = sidecar()
    with pytest.raises(ConfigurationError, match="artifact storage"):
        export(executor_with(provider_with(handler), store=None))
    assert not any(call.url.path == "/render" for call in calls)


def test_toolchain_upgrade_changes_the_cache_key(tmp_path: Path) -> None:
    handler, _ = sidecar()
    executor = executor_with(
        provider_with(handler), store=FilesystemArtifactStore(tmp_path), cache=MemoryCache()
    )
    before = export(executor).provenance.cache_key

    original_versions = dict(VERSIONS)
    VERSIONS["latex_kernel"] = "2026-06-01 pl0"
    try:
        after = export(executor)
    finally:
        VERSIONS.clear()
        VERSIONS.update(original_versions)

    assert after.provenance.cache_key != before
    assert not after.provenance.cache_hit


# ── Sidecar server ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def server() -> Any:
    spec = importlib.util.spec_from_file_location("pdf_ua_render_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(server: Any) -> TestClient:
    return TestClient(server.app)


def post_ast(client: TestClient, body: bytes, **data: str) -> httpx.Response:
    return client.post("/render", files={"file": ("ast.json", body, "application/json")}, data=data)


RAW_LATEX = {"t": "RawInline", "c": ["latex", "\\directlua{io.open('/etc/passwd')}"]}


@pytest.mark.parametrize(
    "location",
    [
        {"blocks": [{"t": "BlockQuote", "c": [{"t": "Para", "c": [RAW_LATEX]}]}]},
        {"meta": {"title": {"t": "MetaInlines", "c": [RAW_LATEX]}}},
        {"meta": {"author": {"t": "MetaList", "c": [{"t": "MetaInlines", "c": [RAW_LATEX]}]}}},
        {"meta": {"date": {"t": "MetaBlocks", "c": [{"t": "RawBlock", "c": ["tex", "\\relax"]}]}}},
    ],
    ids=["nested-block", "meta-title", "meta-author", "meta-date"],
)
def test_server_rejects_raw_tex_anywhere_in_the_ast(
    client: TestClient, location: dict[str, Any]
) -> None:
    response = post_ast(client, json.dumps({**AST, **location}).encode())
    assert response.status_code == 422
    assert "raw TeX" in response.json()["detail"]


@pytest.mark.parametrize("depth", [600, 100_000], ids=["over-limit", "past-recursion-limit"])
def test_server_rejects_a_deeply_nested_ast(client: TestClient, depth: int) -> None:
    body = b'{"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": ' + b"[" * depth
    body += b"]" * depth + b"}"
    assert post_ast(client, body).status_code == 400


def test_server_accepts_raw_html_which_latex_ignores(server: Any) -> None:
    node = {"t": "RawBlock", "c": ["html", "<br>"]}
    assert server._raw_tex_nodes([node]) == 0


@pytest.mark.parametrize(
    "body",
    [b"", b"not json", b'{"blocks": []}', b"[]"],
    ids=["empty", "invalid-json", "no-api-version", "not-an-object"],
)
def test_server_rejects_payloads_that_are_not_an_ast(client: TestClient, body: bytes) -> None:
    assert post_ast(client, body).status_code == 400


def test_server_rejects_an_invalid_language_tag(client: TestClient) -> None:
    response = post_ast(client, AST_BYTES, lang="pt-BR}\\evil")
    assert response.status_code == 400


def test_server_reports_pandoc_failures_as_unprocessable(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_missing_components", lambda: [])

    def failing_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 43, b"", b"! Undefined control sequence.")

    monkeypatch.setattr(server.subprocess, "run", failing_run)
    response = post_ast(client, AST_BYTES)
    assert response.status_code == 422
    assert "Undefined control sequence" in response.json()["detail"]


def test_server_confines_tex_writes_and_disables_shell_escape(server: Any, tmp_path: Path) -> None:
    env = server._sandboxed_env(tmp_path)
    assert env["openout_any"] == "p"
    assert env["shell_escape"] == "f"
    assert env["TMPDIR"] == str(tmp_path)


def test_server_health_fails_without_the_toolchain(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_missing_components", lambda: ["lualatex"])
    assert client.get("/health").status_code == 503


def test_template_defines_what_recent_pandoc_and_longtable_need() -> None:
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    # Pandoc writes \def\LTcaptype{none} for uncaptioned tables and sizes
    # columns with calc expressions; both broke after the TeX Live 2025 update.
    assert "\\newcounter{none}" in template
    assert "calc" in template
    assert "pdfstandard=ua-1" in template


# ── Formula alternate text (filters/formula_alt.lua) ────────────────────


def test_server_renders_through_the_formula_alt_filter(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_missing_components", lambda: [])
    commands: list[list[str]] = []

    def capture(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 43, b"", b"stop here")

    monkeypatch.setattr(server.subprocess, "run", capture)
    post_ast(client, AST_BYTES)

    assert f"--lua-filter={server.FORMULA_ALT_FILTER_PATH}" in commands[0]


def test_toolchain_reports_the_formula_alt_filter_digest(server: Any) -> None:
    server.toolchain.cache_clear()
    try:
        digest = server.toolchain()["formula_alt_filter_sha256"]
    finally:
        server.toolchain.cache_clear()
    assert digest == hashlib.sha256(server.FORMULA_ALT_FILTER_PATH.read_bytes()).hexdigest()


def test_server_health_fails_without_the_formula_alt_filter(
    server: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(server, "FORMULA_ALT_FILTER_PATH", tmp_path / "missing.lua")
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert any("filter" in item for item in server._missing_components())


def test_formula_alt_filter_drops_every_tex_special() -> None:
    """The alternate text is the filter's only raw TeX: no special may survive."""
    source = (SERVER_PATH.parent / "filters" / "formula_alt.lua").read_text(encoding="utf-8")
    literal = re.search(r'TEX_SPECIALS = "(.*)"', source).group(1)
    # Undo the Lua string escape (\\ -> \) and the pattern escape (%% -> %).
    pattern_class = literal.replace("\\\\", "\\").replace("%%", "%")
    for char in "\\{}$&#^_%~":
        assert char in pattern_class, char


def test_template_defines_the_formula_alt_command() -> None:
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    assert "\\NewDocumentCommand \\AccessiliaFormulaAlt" in template
    assert "\\l__math_content_template_tl" in template
