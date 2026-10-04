"""Explicit provider selection through REST, adapter HTTP and normalization."""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.teleocr import TeleOCRProvider


def test_teleocr_raster_request_retains_provenance(monkeypatch) -> None:
    def backend(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "1.2b", "model_revision": "fixed"})
        if request.url.path == "/predict":
            return httpx.Response(
                200,
                json={
                    "size": [100, 200],
                    "blocks": [
                        {"type": "page_number", "bbox": [0.1, 0.8, 0.2, 0.9], "content": "3"},
                        {"type": "equation", "bbox": [0.1, 0.2, 0.8, 0.3], "content": "x^2"},
                    ],
                },
            )
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(
        TeleOCRProvider,
        "_client",
        lambda self, timeout=None: httpx.Client(
            base_url=self.base_url,
            transport=httpx.MockTransport(backend),
        ),
    )
    root = Path(__file__).resolve().parents[2]
    providers = ProviderRegistry(
        [
            ProviderDescriptor(
                id="teleocr",
                endpoint="http://teleocr:5005",
                capabilities=["document.structure.extract"],
            )
        ]
    )
    app = create_app(CapabilityRegistry.from_directory(root / "capabilities"), providers)
    with TestClient(app) as client:
        response = client.post(
            "/v1/capabilities/document.structure.extract:execute",
            files={"file": ("page.png", b"image", "image/png")},
            data={"provider": "teleocr"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "teleocr"
    assert body["provenance"]["model_versions"]["model_revision"] == "fixed"
    elements = body["document"]["elements"]
    assert elements[0]["raw_label"] == "page_number"
    assert elements[0]["confidence"] is None
    assert elements[1]["type"] == "formula" and elements[1]["text"] == "x^2"
    assert elements[1]["provenance"][0]["bbox"]["left"] == 10
