"""Unit tests for PureMediaInspectProvider and media.inspect capability."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.media_inspect import PureMediaInspectProvider, _inspect_media


@pytest.fixture
def provider_descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        id="pure-media-inspect",
        version="1.0",
        transport="in_process",
        capabilities=["media.inspect"],
        media_types=[
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/tiff",
            "image/bmp",
            "application/pdf",
        ],
    )


def test_inspect_jpeg_standalone_image() -> None:
    # Create a 1920x1080 JPEG image (landscape - aspect ratio 1.7778)
    buf = io.BytesIO()
    img = Image.new("RGB", (1920, 1080), color=(73, 109, 137))
    img.save(buf, format="JPEG")
    payload = buf.getvalue()

    result = _inspect_media(payload, "planisferio.jpg", "image/jpeg")

    assert result["is_image"] is True
    assert result["is_pdf"] is False
    assert result["page_count"] == 1
    assert result["dimensions"]["width"] == 1920
    assert result["dimensions"]["height"] == 1080
    assert result["dimensions"]["aspect_ratio"] == 1.7778
    assert result["color_mode"] == "RGB"
    assert result["format"] == "JPEG"
    assert result["artifact_nature"] == "standalone_image"
    assert result["requires_visual_inspection"] is True


def test_inspect_scanned_page_image() -> None:
    # Create an image with portrait A4 aspect ratio (595 x 842 -> aspect ~0.7067)
    buf = io.BytesIO()
    img = Image.new("L", (595, 842), color=255)
    img.save(buf, format="PNG")
    payload = buf.getvalue()

    result = _inspect_media(payload, "scanned_doc.png", "image/png")

    assert result["is_image"] is True
    assert result["is_pdf"] is False
    assert result["dimensions"]["width"] == 595
    assert result["dimensions"]["height"] == 842
    assert result["artifact_nature"] == "scanned_page"
    assert result["requires_visual_inspection"] is True


def test_inspect_pdf_document() -> None:
    # Minimal PDF payload
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer\n<< /Root 1 0 R >>\nstartxref\n120\n%%EOF\n"
    )

    result = _inspect_media(pdf_content, "document.pdf", "application/pdf")

    assert result["is_pdf"] is True
    assert result["is_image"] is False
    assert result["artifact_nature"] == "paginated_document"
    assert result["requires_visual_inspection"] is False
    assert result["page_count"] >= 1


def test_inspect_unknown_bytes() -> None:
    payload = b"not a valid image or pdf content"
    result = _inspect_media(payload, "unknown.dat", "application/octet-stream")

    assert result["is_image"] is False
    assert result["is_pdf"] is False
    assert result["artifact_nature"] == "unknown"
    assert result["requires_visual_inspection"] is False


def test_provider_execute_and_health(provider_descriptor: ProviderDescriptor) -> None:
    provider = PureMediaInspectProvider(provider_descriptor)
    health = provider.health()
    assert health.healthy is True
    assert health.provider == "pure-media-inspect"

    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    img.save(buf, format="PNG")
    payload = buf.getvalue()

    res = provider.execute(
        capability_id="media.inspect",
        payload=payload,
        filename="icon.png",
        media_type="image/png",
    )

    assert res.backend == "pure-python"
    assert res.document["is_image"] is True
    assert res.document["dimensions"]["width"] == 100
    assert res.document["dimensions"]["height"] == 100
    assert res.document["artifact_nature"] == "standalone_image"
