"""The sidecar's /render/docx route against a real Pandoc.

Lives outside ``tests/unit`` because it needs pandoc on PATH, which the unit CI
job does not install; inside the sidecar image the build's smoke test covers
the same path.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("pandoc") is None, reason="needs pandoc"),
]

SERVER_PATH = Path(__file__).resolve().parents[2] / "docker" / "pdf-ua-render" / "server.py"
SECRET = Path(__file__)
ALT_TEXT = "Gráfico-de-vendas"
ATTR: list = ["", [], []]


def cell(text: str) -> list:
    return [ATTR, {"t": "AlignDefault"}, 1, 1, [{"t": "Plain", "c": [{"t": "Str", "c": text}]}]]


def row(*texts: str) -> list:
    return [ATTR, [cell(text) for text in texts]]


AST = {
    "pandoc-api-version": [1, 23, 1],
    "meta": {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "Relatório"}]}},
    "blocks": [
        {"t": "Header", "c": [1, ["intro", [], []], [{"t": "Str", "c": "Introdução"}]]},
        {"t": "Para", "c": [{"t": "Math", "c": [{"t": "InlineMath"}, "E=mc^2"]}]},
        {
            "t": "Table",
            "c": [
                ATTR,
                [None, []],
                [[{"t": "AlignDefault"}, {"t": "ColWidthDefault"}]] * 2,
                [ATTR, [row("Mês", "Total")]],
                [[ATTR, 0, [], [row("Jan", "10")]]],
                [ATTR, []],
            ],
        },
        # A local file as an image source: the sandbox must keep it out and
        # leave the alt text in its place.
        {
            "t": "Para",
            "c": [{"t": "Image", "c": [ATTR, [{"t": "Str", "c": ALT_TEXT}], [str(SECRET), ""]]}],
        },
    ],
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    spec = importlib.util.spec_from_file_location("pdf_ua_render_server_docx_it", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return TestClient(module.app)


def test_server_renders_an_accessible_docx_without_local_files(client: TestClient) -> None:
    response = client.post(
        "/render/docx",
        files={"file": ("ast.json", json.dumps(AST).encode(), "application/json")},
        data={"lang": "pt-BR"},
    )

    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    document = archive.read("word/document.xml").decode("utf-8")
    styles = archive.read("word/styles.xml").decode("utf-8")
    core = archive.read("docProps/core.xml").decode("utf-8")
    assert '<w:pStyle w:val="Heading1" />' in document
    assert "<w:tblHeader" in document
    assert "<m:oMath>" in document
    assert ALT_TEXT in document
    assert 'w:val="pt-BR"' in styles
    assert "<dc:language>pt-BR</dc:language>" in core
    assert not [name for name in archive.namelist() if name.startswith("word/media/")]
