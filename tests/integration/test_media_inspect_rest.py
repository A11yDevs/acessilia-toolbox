"""`media.inspect` through the REST facade with the real in-process provider.

Unlike the stubbed facade test, this exercises the shipped capability manifest,
the real `create_adapter` dispatch, and the real Pillow-backed provider, so the
HTTP request/response contract is verified end to end.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITY = "media.inspect"
ENDPOINT = f"/v1/capabilities/{CAPABILITY}:execute"


def png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client() -> TestClient:
    providers = ProviderRegistry(
        [
            ProviderDescriptor(
                id="pure-media-inspect",
                transport="in_process",
                capabilities=[CAPABILITY],
            )
        ]
    )
    app = create_app(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"), providers
    )
    return TestClient(app)


def test_capability_is_advertised(client: TestClient) -> None:
    response = client.get("/v1/capabilities")

    assert response.status_code == 200
    summary = next(c for c in response.json() if c["id"] == CAPABILITY)
    assert summary["output_schema"] == "artifact/media-inspection@1"
    assert summary["providers"] == ["pure-media-inspect"]


def test_image_is_classified_over_http(client: TestClient) -> None:
    response = client.post(
        ENDPOINT,
        files={"file": ("diagram.png", png_bytes(1920, 1080), "image/png")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["provider"] == "pure-media-inspect"
    document = body["document"]
    assert document["artifact_nature"] == "standalone_image"
    assert document["dimensions"]["width"] == 1920
    assert document["requires_visual_inspection"] is True


def test_request_parameters_reach_the_provider(client: TestClient) -> None:
    response = client.post(
        ENDPOINT,
        files={"file": ("page.png", png_bytes(595, 842), "image/png")},
        data={"parameters": '{"min_dimension_px": 900}'},
    )

    assert response.status_code == 200, response.text
    document = response.json()["document"]
    # 595x842 is in the scanned-page aspect band but below the raised threshold,
    # so the caller's parameter must have been applied.
    assert document["artifact_nature"] == "standalone_image"


def test_declared_media_type_outside_the_contract_is_rejected(client: TestClient) -> None:
    response = client.post(
        ENDPOINT,
        files={"file": ("blob.bin", b"not an artifact", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"


def test_undecodable_payload_is_reported_without_guessing(client: TestClient) -> None:
    """A declared-but-lying media type must yield `unknown`, not a fabricated page."""
    response = client.post(
        ENDPOINT,
        files={"file": ("page.png", b"not an image", "image/png")},
    )

    assert response.status_code == 200, response.text
    document = response.json()["document"]
    assert document["artifact_nature"] == "unknown"
    assert document["dimensions"] is None
    assert document["media_type"] == "application/octet-stream"
    assert document["declared_media_type"] == "image/png"
