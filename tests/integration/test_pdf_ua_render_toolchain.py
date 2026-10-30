"""pdf-ua-render sidecar against the real Pandoc + LuaLaTeX toolchain.

Lives outside ``tests/unit`` because it needs pandoc and lualatex on PATH,
which the unit CI job does not install; inside the sidecar image the build's
smoke test covers the same path.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_math_letters_reach_the_text_layer_as_base_letters(server: Any) -> None:
    """#106: unicode-math's italic letters must not reach readers as plane-1 symbols."""
    ast = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [
            {
                "t": "Para",
                "c": [
                    {
                        "t": "Math",
                        "c": [
                            {"t": "InlineMath"},
                            r"v(t) = s'(t),\ \alpha h \in \mathbb{R},\ "
                            r"\mathfrak{g},\ \boldsymbol{x}",
                        ],
                    }
                ],
            }
        ],
    }
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
    layer = checker.text_layer(response.content)
    assert checker.math_italic_letters(layer) == set()
    assert {"v", "t", "s", "h", "x", "\u03b1"} <= layer
    # Double-struck and fraktur letters keep their meaning.
    assert {"\u211d", "\U0001d524"} <= layer
