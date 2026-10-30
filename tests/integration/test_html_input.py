"""HTML input crosses the shipped contracts and the real Docling adapter."""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.docling import DoclingProvider
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

HTML = b"<!doctype html><html><body><h1>HTML title</h1><p>HTML body</p></body></html>"
ROOT = Path(__file__).resolve().parents[2]


def test_html_is_advertised_by_contracts_and_docling():
    caps = CapabilityRegistry.from_directory(ROOT / "capabilities")
    providers = ProviderRegistry.from_file(ROOT / "providers-config.yaml")
    assert "text/html" in caps.get("document.structure.extract").input.media_types
    assert "text/html" in providers.get("docling").media_types
    assert "text/html" in caps.get("media.inspect").input.media_types
    assert "text/html" in providers.get("pure-media-inspect").media_types


def test_html_structure_and_neutral_media_inspection(tmp_path, monkeypatch):
    converted = []

    def serve(request):
        if request.url.path == "/version":
            return httpx.Response(200, json={"docling-serve": "1.32.0"})
        assert request.url.path == "/v1/convert/file"
        assert HTML in request.content
        assert b"text/html" in request.content
        converted.append(request)
        document = {
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": "title",
                    "text": "HTML title",
                    "level": 1,
                    "prov": [],
                },
                {"self_ref": "#/texts/1", "label": "text", "text": "HTML body", "prov": []},
            ],
            "pages": {},
        }
        return httpx.Response(200, json={"document": {"json_content": document}})

    monkeypatch.setattr(
        DoclingProvider,
        "_client",
        lambda self, timeout=None: httpx.Client(
            base_url=self.base_url, transport=httpx.MockTransport(serve)
        ),
    )
    providers = ProviderRegistry(
        [
            ProviderDescriptor(
                id="docling",
                endpoint="http://docling.test",
                capabilities=["document.structure.extract"],
            ),
            ProviderDescriptor(
                id="pure-media-inspect", transport="in_process", capabilities=["media.inspect"]
            ),
        ]
    )
    app = create_app(
        CapabilityRegistry.from_directory(ROOT / "capabilities"),
        providers,
        store=FilesystemArtifactStore(tmp_path),
    )
    with TestClient(app) as client:
        inspection = client.post(
            "/v1/capabilities/media.inspect:execute",
            files={"file": ("input.html", HTML, "text/html")},
            data={"provider": "pure-media-inspect"},
        )
        extraction = client.post(
            "/v1/capabilities/document.structure.extract:execute",
            files={"file": ("input.html", HTML, "text/html")},
            data={"provider": "docling"},
        )
    assert inspection.status_code == 200, inspection.text
    media = inspection.json()["document"]
    assert media["declared_media_type"] == "text/html"
    assert media["dimensions"] is None
    assert media["requires_visual_inspection"] is False
    assert extraction.status_code == 200, extraction.text
    document = extraction.json()["document"]
    assert document["source"]["media_type"] == "text/html"
    assert {e["text"] for e in document["elements"]} == {"HTML title", "HTML body"}
    assert len(converted) == 1
