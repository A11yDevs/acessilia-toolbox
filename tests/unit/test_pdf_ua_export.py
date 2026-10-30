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


@pytest.mark.parametrize("artifact_index", [0, 1], ids=["json", "pdf"])
def test_cache_hit_with_a_deleted_artifact_renders_again(
    tmp_path: Path, artifact_index: int
) -> None:
    handler, calls = sidecar()
    store = FilesystemArtifactStore(tmp_path)
    executor = executor_with(provider_with(handler), store=store, cache=MemoryCache())

    first = export(executor)
    store._path(first.artifacts[artifact_index].artifact_id).unlink()
    second = export(executor)

    assert not second.provenance.cache_hit
    assert sum(call.url.path == "/render" for call in calls) == 2
    assert store.get(second.document["artifact_id"]) == PDF_BYTES
    assert second.document["artifact_id"] == first.document["artifact_id"]
    for artifact in second.artifacts:
        payload, ref = executor.retrieve_artifact(artifact.artifact_id)
        assert fingerprint_bytes(payload) == ref.artifact_id
    assert export(executor).provenance.cache_hit


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


def test_template_leaves_headings_unnumbered_by_default() -> None:
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    # Without this the article class tags "1", "1.1" inside each heading.
    # The numbersections metadata stays an opt-in, as in Pandoc's template.
    assert (
        "$if(numbersections)$\n"
        "\\setcounter{secnumdepth}{$if(secnumdepth)$$secnumdepth$$else$5$endif$}\n"
        "$else$\n"
        "\\setcounter{secnumdepth}{-\\maxdimen}\n"
        "$endif$\n"
    ) in template


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


def test_template_guards_the_internal_math_collect_command() -> None:
    """\\MathCollectTrue is latex-lab internal: its absence must fail by name."""
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    assert "\\NewDocumentCommand \\AccessiliaFormulaCollect { } { \\MathCollectTrue }" in template
    assert "\\cs_if_exist:NF \\MathCollectTrue" in template
    source = (SERVER_PATH.parent / "filters" / "formula_alt.lua").read_text(encoding="utf-8")
    # The filter goes through the template's command, never the internal one.
    assert "\\\\AccessiliaFormulaCollect" in source
    assert "MathCollectTrue" not in source


def test_template_sets_math_with_unicode_math_after_fontspec() -> None:
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    # unicode-math needs fontspec first, and conflicts with amssymb.
    assert "\\usepackage{fontspec}" in template
    assert "\\usepackage{unicode-math}" in template
    assert template.index("\\usepackage{fontspec}") < template.index(
        "\\usepackage{unicode-math}"
    ), "unicode-math must load after fontspec"
    assert "\\setmathfont{texgyredejavu-math.otf}" in template
    assert "\\usepackage{amsmath}" in template
    assert not re.search(r"\\usepackage(\[[^]]*\])?\{[^}]*amssymb", template)
    # amssymb names unicode-math lacks: undefined, one would fail the whole render.
    for name in ("square", "lozenge", "varpropto", "nleqslant", "thicksim"):
        assert f"\\providecommand\\{name}{{" in template, name
    assert "\\let\\smallsetminus\\setminus" in template
    assert "\\setmathfont{STIXTwoMath-Regular.otf}[range=" in template


def test_template_maps_math_letters_to_base_letters_before_any_font() -> None:
    """#106: the ToUnicode patch only reaches fonts loaded after it is registered."""
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    callback = '"luaotfload.patch_font"'
    assert callback in template
    assert template.index(callback) < template.index("\\usepackage{fontspec}")
    # \directlua reads its argument as one line of TeX tokens: a Lua comment
    # would swallow the rest, and %, # and ~ are TeX specials.
    start = template.index("\\directlua{")
    end = template.index('"accessilia.math_letter_tounicode")', start)
    lua = template[start + len("\\directlua{") : end]
    assert "--" not in lua
    assert not set("%#~\\") & set(lua)


def _smoke_check() -> Any:
    spec = importlib.util.spec_from_file_location(
        "pdf_ua_smoke_check", SERVER_PATH.parent / "smoke_check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_check_reads_hex_and_literal_alternate_texts() -> None:
    """The build-time /Alt check must not miss an /Alt the writer encodes differently."""
    module = _smoke_check()

    utf16 = "<FEFF" + "x pertence".encode("utf-16-be").hex().upper() + ">"
    pdf = f"/Alt {utf16} /Alt (a \\(b\\) (c) \\101) /Alt <41424>".encode()

    assert module.alternate_texts(pdf) == ["x pertence", "a (b) (c) A", "AB@"]
    # A hex string the writer wraps across lines.
    assert module.alternate_texts(b"/Alt <FEFF 0061\n 0062>") == ["ab"]
    # UTF-16 little-endian, by its byte order mark.
    assert module.alternate_texts(b"/Alt <FFFE6100>") == ["a"]
    # Backslash-EOL continues the line; a bare EOL of any kind reads as LF.
    assert module.alternate_texts(b"/Alt (a\\\nb) /Alt (c\\\r\nd) /Alt (e\r\nf\rg)") == [
        "ab",
        "cd",
        "e\nf\ng",
    ]


def test_smoke_check_reads_only_formula_alternate_texts() -> None:
    """An /Alt on another element must not stand in for a missing Formula."""
    module = _smoke_check()
    pdf = (
        b"7 0 obj\n<< /Type /StructElem /S /Figure /Alt (figura) >>\nendobj\n"
        b"8 0 obj\n<< /Type /StructElem /C /inline /Alt (x igual a 1) /S /Formula"
        b" /K <</Type /MCR /MCID 1>> >>\nendobj\n"
        b"9 0 obj\n<< /Type /StructElem /S /Formula /K 2 >>\nendobj\n"
        # The role map names Formula without being a structure element.
        b"10 0 obj\n<< /Type /StructTreeRoot /RoleMap << /Formula /Formula >>"
        b" /Alt (raiz) >>\nendobj\n"
    )

    assert module.formula_alternate_texts(pdf) == ["x igual a 1", ""]


def test_smoke_check_reads_the_tounicode_text_layer() -> None:
    """The build-time \\neq check reads bfchar and both bfrange forms of a CMap."""
    module = _smoke_check()
    pdf = (
        b"/CIDInit /ProcSet findresource begin begincmap\n"
        b"2 beginbfchar\n<0F38> <2260>\n<01D5> <D835DC60>\nendbfchar\n"
        b"2 beginbfrange\n<0010> <0012> <0041>\n<0020> <0021> [<0063> <003D0338>]\nendbfrange\n"
        b"endcmap\n"
        # Outside a CMap, a hex pair is not a mapping (e.g. a content stream).
        b"beginbfchar <0001> <0338> endbfchar"
    )

    assert module.text_layer(pdf) == {"\u2260", "\U0001d460", "A", "B", "C", "c", "=\u0338"}


def test_smoke_check_skips_malformed_tounicode_targets() -> None:
    """A target with no UTF-16 text is skipped, not a traceback in the image build."""
    module = _smoke_check()
    pdf = (
        b"begincmap\n"
        b"2 beginbfchar\n<0001> <D835>\n<0002> <260>\nendbfchar\n"
        # FFFE, FFFF, then 10000: five digits, odd length.
        b"1 beginbfrange\n<0010> <0012> <FFFE>\nendbfrange\n"
        b"1 beginbfchar\n<0003> <2260>\nendbfchar\n"
        b"endcmap\n"
    )

    assert module.text_layer(pdf) == {"￾", "￿", "≠"}


def test_smoke_check_finds_italic_math_letters_in_the_text_layer() -> None:
    module = _smoke_check()

    layer = {"v", "\U0001d463", "\u210e", "\U0001d6fc", "\U0001d482", "\u211d", "\U0001d524"}

    # Italic v, italic h, italic alpha and bold italic a; double-struck R and
    # fraktur g keep their code points by design.
    assert module.math_italic_letters(layer) == {
        "\U0001d463", "\u210e", "\U0001d6fc", "\U0001d482"
    }
    assert module.math_italic_letters({"v(t) = s\u2032(t)", "\u211d"}) == set()


def test_template_tags_lists_with_pdf17_list_numbering() -> None:
    """latex-lab writes the PDF 2.0 values (Ordered...); PDF/UA-1 is PDF 1.7."""
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    for environment, value in (
        ("itemize", "Disc"),
        ("enumerate", "Decimal"),
        ("description", "None"),
        ("list", "None"),
    ):
        line = f"role/new-attribute = {{{environment}}}{{/O /List /ListNumbering/{value}}}"
        assert line in template, line
    assert template.index("ListNumbering/Decimal") < template.index("\\begin{document}")


def test_template_does_not_hyphenate() -> None:
    """A hyphenated word reaches the text layer as two words (in- / stante)."""
    template = (SERVER_PATH.parent / "templates" / "pdf_ua.tex").read_text(encoding="utf-8")
    assert "\\hyphenpenalty=10000" in template
    assert "\\exhyphenpenalty=10000" in template
    assert "\\raggedright" in template


def test_smoke_check_reads_list_numbering_in_objects_and_streams() -> None:
    import zlib

    module = _smoke_check()
    compressed = zlib.compress(b"<< /O /List /ListNumbering /Ordered >>")
    pdf = (
        b"5 0 obj\n<< /O /List /ListNumbering /Decimal >>\nendobj\n"
        b"6 0 obj\n<< /Length 9 >>\nstream\n" + compressed + b"\nendstream\nendobj\n"
    )

    assert sorted(module.list_numbering_values(pdf)) == ["Decimal", "Ordered"]
    assert set(module.list_numbering_values(pdf)) - module.PDF17_LIST_NUMBERING == {"Ordered"}


def test_smoke_check_rejects_pdf20_list_numbering() -> None:
    """The guard has to fail on latex-lab's Ordered/Unordered, not only read them."""
    module = _smoke_check()

    assert module.list_numbering_errors(["Decimal", "Ordered"], require_lists=False) == [
        "ListNumbering values outside PDF 1.7 (PDF/UA-1): ['Ordered']"
    ]
    assert module.list_numbering_errors(["Disc", "Unordered"], require_lists=False) == [
        "ListNumbering values outside PDF 1.7 (PDF/UA-1): ['Unordered']"
    ]
    assert module.list_numbering_errors(["Decimal", "Disc"], require_lists=False) == []


def test_smoke_check_requires_lists_when_asked() -> None:
    """--require-lists must fail a smoke whose list tagging disappeared."""
    module = _smoke_check()

    assert module.list_numbering_errors([], require_lists=True) == [
        "no ListNumbering in a smoke that has lists: latex-lab stopped tagging them"
    ]
    assert module.list_numbering_errors([], require_lists=False) == []
    assert module.list_numbering_errors(["Decimal", "Disc"], require_lists=True) == []
