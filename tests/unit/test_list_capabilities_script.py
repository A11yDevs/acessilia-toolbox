"""scripts/list-capabilities.sh against a local HTTP server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "list-capabilities.sh"
API_KEY = "test-key"

CAPABILITIES = [
    {
        "id": "document.structure.extract",
        "version": 1,
        "providers": ["docling", "mineru"],
        "declared_providers": ["docling", "mineru"],
        "latest": True,
    },
    {
        # An older capability version must not be listed by the script.
        "id": "pdf.render",
        "version": 1,
        "providers": [],
        "declared_providers": ["pymupdf-pdf"],
        "latest": False,
    },
    {
        "id": "pdf.render",
        "version": 2,
        "providers": ["pymupdf-pdf"],
        "declared_providers": ["pymupdf-pdf"],
        "latest": True,
    },
]

PROVIDERS = [
    {
        "id": "docling",
        "version": "1.37.0",
        "transport": "http",
        "endpoint": "http://docling-serve:5001",
    },
    {
        "id": "mineru",
        "version": "4.0.12",
        "transport": "http",
        "endpoint": "http://mineru-serve:8000",
    },
    {
        "id": "pymupdf-pdf",
        "version": "1.24",
        "transport": "in_process",
        "endpoint": None,
    },
]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {API_KEY}":
            self.send_error(401, "Missing or invalid token")
            return
        payload = {
            "/v1/capabilities": CAPABILITIES,
            "/v1/providers": PROVIDERS,
        }.get(self.path)
        if payload is None:
            self.send_error(404)
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def toolbox_url() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()


def _environment(base_url: str, *, key: str | None = API_KEY) -> dict[str, str]:
    env = os.environ.copy()
    env["TOOLBOX_BASE_URL"] = base_url
    env.pop("BASE", None)
    env.pop("KEY", None)
    if key is None:
        env.pop("TOOLBOX_API_KEY", None)
    else:
        env["TOOLBOX_API_KEY"] = key
    return env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


pytestmark = pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("jq") is None,
    reason="list-capabilities.sh requires curl and jq",
)


def test_lists_latest_capabilities_with_provider_versions(toolbox_url: str) -> None:
    result = _run(_environment(toolbox_url))

    assert result.returncode == 0, result.stderr
    rows = result.stdout.splitlines()
    assert rows[0] == "capability\tcap_ver\tprovider\tprovider_ver\ttransport\tendpoint"
    assert (
        "document.structure.extract\t1\tdocling\t1.37.0\thttp\t"
        "http://docling-serve:5001"
    ) in rows
    assert (
        "document.structure.extract\t1\tmineru\t4.0.12\thttp\t"
        "http://mineru-serve:8000"
    ) in rows
    assert "pdf.render\t2\tpymupdf-pdf\t1.24\tin_process\t-" in rows
    assert not any(row.startswith("pdf.render\t1\t") for row in rows)


def test_reports_http_error(toolbox_url: str) -> None:
    result = _run(_environment(toolbox_url, key="wrong-key"))

    assert result.returncode != 0
    assert "HTTP 401" in result.stderr


def test_requires_a_key_when_not_interactive(toolbox_url: str) -> None:
    result = _run(_environment(toolbox_url, key=None))

    assert result.returncode != 0
    assert "TOOLBOX_API_KEY" in result.stderr
