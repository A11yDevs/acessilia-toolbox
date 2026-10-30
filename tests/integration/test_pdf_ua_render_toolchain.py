"""pdf-ua-render sidecar against the real Pandoc + LuaLaTeX toolchain.

Lives outside ``tests/unit`` because it needs pandoc and lualatex on PATH,
which the unit CI job does not install; inside the sidecar image the build's
smoke test covers the same path.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("pandoc") is None or shutil.which("lualatex") is None,
        reason="needs pandoc and lualatex",
    ),
]

SERVER_PATH = Path(__file__).resolve().parents[2] / "docker" / "pdf-ua-render" / "server.py"
AST = {
    "pandoc-api-version": [1, 23, 1],
    "meta": {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "Relatório"}]}},
    "blocks": [{"t": "Para", "c": [{"t": "Str", "c": "Olá"}]}],
}


@pytest.fixture(scope="module")
def server() -> Any:
    spec = importlib.util.spec_from_file_location("pdf_ua_render_server_it", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_server_renders_a_tagged_pdf(server: Any) -> None:
    response = TestClient(server.app).post(
        "/render",
        files={"file": ("ast.json", json.dumps(AST).encode(), "application/json")},
    )
    assert response.status_code == 200, response.text[:500]
    assert response.content.startswith(b"%PDF-")
    assert b"/StructTreeRoot" in response.content or b"/MarkInfo" in response.content


def test_server_tags_every_formula_in_a_table(server: Any) -> None:
    fixture = SERVER_PATH.parent / "table-smoke.json"
    response = TestClient(server.app).post(
        "/render",
        files={"file": ("table.json", fixture.read_bytes(), "application/json")},
    )
    assert response.status_code == 200, response.text[:500]

    checker_path = SERVER_PATH.parent / "smoke_check.py"
    spec = importlib.util.spec_from_file_location("pdf_ua_smoke_check_it", checker_path)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    # One Formula per formula: header cell, data cell, and the empty alt, which
    # keeps latex-lab's default text instead of losing the element.
    alts = checker.formula_alternate_texts(response.content)
    assert len(alts) == 3, alts
    assert alts[:2] == ["t igual a 2 mais h no cabeçalho", "x diferente de um na célula"]
    assert alts[2].startswith("LaTeX formula starts"), alts[2]


def _render_layer(server: Any, inlines: list[dict[str, Any]]) -> tuple[Any, set[str]]:
    """Render one paragraph; the smoke checker and the PDF's text layer."""
    ast = {"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": [{"t": "Para", "c": inlines}]}
    response = TestClient(server.app).post(
        "/render",
        files={"file": ("math.json", json.dumps(ast).encode(), "application/json")},
    )
    assert response.status_code == 200, response.text[:500]

    checker_path = SERVER_PATH.parent / "smoke_check.py"
    spec = importlib.util.spec_from_file_location("pdf_ua_smoke_check_letters", checker_path)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker, checker.text_layer(response.content)


def _math(latex: str) -> dict[str, Any]:
    return {"t": "Math", "c": [{"t": "InlineMath"}, latex]}


def test_math_letters_reach_the_text_layer_as_base_letters(server: Any) -> None:
    """#106: unicode-math's italic letters must not reach readers as plane-1 symbols."""
    checker, layer = _render_layer(
        server,
        [_math(r"v(t) = s'(t),\ \alpha h \in \mathbb{R},\ \mathfrak{g},\ \boldsymbol{x}")],
    )

    assert checker.math_italic_letters(layer) == set()
    assert {"v", "t", "s", "h", "x", "\u03b1"} <= layer
    # Double-struck and fraktur letters keep their meaning.
    assert {"\u211d", "\U0001d524"} <= layer


def test_greek_variants_and_dotless_letters_reach_their_base_letters(server: Any) -> None:
    """The map's entries computed by offset, each a plane-1 letter without it.

    Italic capitals (U+1D6E4 Γ, U+1D6E9 Θ, U+1D6F3 ϴ, U+1D6FA Ω, U+1D6FB ∇),
    the variants after ω (U+1D715-U+1D71B), the final sigma (U+1D70D),
    dotless i and j (U+1D6A4-5) and bold italic h, Γ and ϖ (U+1D489,
    U+1D71E, U+1D755: the last entry of the map).
    """
    checker, layer = _render_layer(
        server,
        [
            _math(
                r"\symit{\Gamma\Theta\varTheta\Omega\nabla}\ "
                r"\partial\epsilon\vartheta\varkappa\varphi\varrho\varpi\varsigma\ "
                r"\imath\jmath\ \symbfit{h\Gamma\varpi}"
            )
        ],
    )

    assert checker.math_italic_letters(layer) == set()
    capitals = "\u0393\u0398\u03f4\u03a9\u2207"  # Gamma, Theta, Theta symbol, Omega, nabla
    # partial, epsilon, theta, kappa, phi, rho, pi and final sigma variants
    variants = "\u2202\u03f5\u03d1\u03f0\u03c6\u03f1\u03d6\u03c2"
    assert set(capitals + variants + "\u0131\u0237h") <= layer


def test_an_h_written_in_prose_keeps_its_code_point(server: Any) -> None:
    """Only math fonts are remapped: U+210E typed in the text stays U+210E."""
    _, layer = _render_layer(server, [{"t": "Str", "c": "\u210e"}])

    assert "\u210e" in layer
    assert "h" not in layer


def test_lists_carry_pdf17_list_numbering(server: Any) -> None:
    """#332: latex-lab's PDF 2.0 values (Ordered, Unordered) are not PDF/UA-1."""

    def para(text: str) -> list[dict[str, Any]]:
        return [{"t": "Para", "c": [{"t": "Str", "c": text}]}]

    spec = importlib.util.spec_from_file_location(
        "pdf_ua_smoke_check_lists", SERVER_PATH.parent / "smoke_check.py"
    )
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    ast = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [
            {"t": "OrderedList", "c": [[1, {"t": "Decimal"}, {"t": "Period"}],
                                      [para("um"), para("dois")]]},
            {"t": "BulletList", "c": [para("a"), para("b")]},
        ],
    }
    response = TestClient(server.app).post(
        "/render",
        files={"file": ("lists.json", json.dumps(ast).encode(), "application/json")},
    )
    assert response.status_code == 200, response.text[:500]

    values = checker.list_numbering_values(response.content)
    assert set(values) == {"Decimal", "Disc"}


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="needs pdftotext")
def test_words_are_not_hyphenated_across_lines(server: Any, tmp_path) -> None:
    """#332: in- / stante reaches readers as two words; the template does not hyphenate."""
    import subprocess

    words = "instante velocidade aproximação concluímos introduziremos eventualmente " * 12
    ast = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [{"t": "Para", "c": [{"t": "Str", "c": words}]}],
    }
    response = TestClient(server.app).post(
        "/render",
        files={"file": ("words.json", json.dumps(ast).encode(), "application/json")},
    )
    assert response.status_code == 200, response.text[:500]
    pdf = tmp_path / "words.pdf"
    pdf.write_bytes(response.content)

    text = subprocess.run(
        ["pdftotext", "-raw", str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout
    hyphenated = [line for line in text.splitlines() if re.search(r"[^\W\d_]-$", line)]
    assert hyphenated == []
