"""verapdf-validate sidecar against a real veraPDF CLI.

Lives outside ``tests/unit`` because it needs veraPDF (and a JRE), which the
unit CI job does not install; inside the sidecar image the build's smoke test
covers the same path. Point ``VERAPDF_BIN`` at the CLI when it is not on PATH.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

DOCKER = Path(__file__).resolve().parents[2] / "docker"
VERAPDF = os.environ.get("VERAPDF_BIN") or shutil.which("verapdf")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(VERAPDF is None, reason="needs the veraPDF CLI (VERAPDF_BIN or PATH)"),
]


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator() -> TestClient:
    os.environ["VERAPDF_BIN"] = str(VERAPDF)
    return TestClient(
        load("verapdf_validate_server_it", DOCKER / "verapdf-validate" / "server.py").app
    )


def validate(client: TestClient, pdf: bytes) -> dict[str, Any]:
    response = client.post("/validate", files={"file": ("a.pdf", pdf, "application/pdf")})
    assert response.status_code == 200, response.text[:500]
    return response.json()


def test_untagged_pdf_fails_pdf_ua_1(validator: TestClient) -> None:
    sys.path.insert(0, str(DOCKER / "verapdf-validate"))
    smoke = load("verapdf_validate_smoke_it", DOCKER / "verapdf-validate" / "smoke.py")
    report = validate(validator, smoke.minimal_pdf())
    assert report["compliant"] is False
    assert any(failure["clause"] == "7.1" for failure in report["failures"])


@pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("lualatex") is None,
    reason="needs pandoc and lualatex",
)
def test_pdf_from_the_pdf_ua_template_is_compliant(validator: TestClient) -> None:
    """End to end: what document.export.pdf_ua renders must pass PDF/UA-1."""
    renderer = load("pdf_ua_render_server_for_verapdf", DOCKER / "pdf-ua-render" / "server.py")
    ast = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": "Relatório"}]}},
        "blocks": [
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "Título"}]]},
            {"t": "Para", "c": [{"t": "Str", "c": "Olá"}]},
        ],
    }
    rendered = TestClient(renderer.app).post(
        "/render", files={"file": ("ast.json", json.dumps(ast).encode(), "application/json")}
    )
    assert rendered.status_code == 200, rendered.text[:500]

    report = validate(validator, rendered.content)
    assert report["compliant"] is True, report["failures"]
