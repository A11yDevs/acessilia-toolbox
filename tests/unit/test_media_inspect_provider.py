"""pure-media-inspect provider behavior for the media.inspect capability."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from PIL import Image

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.media_inspect import (
    PureMediaInspectProvider,
    _inspect_media,
    _inspect_pdf,
    _resolve_aspect_ratio,
    _resolve_dimension,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "media-inspection@1.json"

CAPABILITY = "media.inspect"
MEDIA_TYPES = [
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/tiff",
    "image/bmp",
    "application/pdf",
]


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "pure-media-inspect",
        "version": "1.0",
        "transport": "in_process",
        "capabilities": [CAPABILITY],
        "media_types": MEDIA_TYPES,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def image_bytes(
    width: int,
    height: int,
    *,
    fmt: str = "PNG",
    mode: str = "RGB",
    frames: int = 1,
) -> bytes:
    buf = io.BytesIO()
    if frames > 1:
        # Frames must differ in content, otherwise multi-frame encoders such as
        # GIF collapse the animation to a single frame.
        frames_list = [
            Image.new(mode, (width, height), (index * 40) % 256)
            for index in range(frames)
        ]
        frames_list[0].save(
            buf, format=fmt, save_all=True, append_images=frames_list[1:]
        )
    else:
        Image.new(mode, (width, height)).save(buf, format=fmt)
    return buf.getvalue()


def pdf_bytes(pages: int = 1, width: int = 595, height: int = 842) -> bytes:
    import pymupdf

    document = pymupdf.open()
    try:
        for _ in range(pages):
            document.new_page(width=width, height=height)
        return document.tobytes()
    finally:
        document.close()


def inspect(payload: bytes, filename: str, media_type: str, **params: Any) -> dict[str, Any]:
    return _inspect_media(payload, filename, media_type, params or None)


# ---------------------------------------------------------------------------
# Container detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "media_type"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
        ("TIFF", "image/tiff"),
        ("BMP", "image/bmp"),
    ],
)
def test_detects_the_media_type_from_the_container(fmt: str, media_type: str) -> None:
    result = inspect(image_bytes(400, 400, fmt=fmt), f"sample.{fmt.lower()}", media_type)

    assert result["media_type"] == media_type
    assert result["format"] == fmt
    assert result["is_image"] is True


def test_extension_does_not_override_the_detected_container() -> None:
    """The payload decides; a mismatched name must not be trusted."""
    payload = image_bytes(300, 200, fmt="JPEG")

    result = inspect(payload, "foto.png", "image/png")

    assert result["media_type"] == "image/jpeg"
    assert result["format"] == "JPEG"
    assert result["declared_media_type"] == "image/png"


def test_unrecognized_container_is_reported_as_unknown() -> None:
    result = inspect(b"not a valid image or pdf content", "unknown.dat", "application/octet-stream")

    assert result["is_image"] is False
    assert result["is_pdf"] is False
    assert result["artifact_nature"] == "unknown"
    assert result["format"] is None
    assert result["media_type"] == "application/octet-stream"
    assert result["requires_visual_inspection"] is False


# ---------------------------------------------------------------------------
# Dimensions and page counts
# ---------------------------------------------------------------------------


def test_single_frame_image_reports_measured_dimensions() -> None:
    result = inspect(image_bytes(1920, 1080, fmt="JPEG"), "planisferio.jpg", "image/jpeg")

    assert result["dimensions"]["width"] == 1920
    assert result["dimensions"]["height"] == 1080
    assert result["dimensions"]["aspect_ratio"] == pytest.approx(1.7778, abs=1e-4)
    assert result["dimensions_source"] == "pillow"
    assert result["page_count"] == 1
    assert result["page_count_source"] == "pillow"


@pytest.mark.parametrize(
    ("fmt", "mode"),
    [("TIFF", "RGB"), ("GIF", "P"), ("PNG", "RGBA")],
)
def test_multi_frame_containers_are_paginated_documents(fmt: str, mode: str) -> None:
    payload = image_bytes(600, 800, fmt=fmt, mode=mode, frames=3)

    result = inspect(payload, f"multi.{fmt.lower()}", f"image/{fmt.lower()}")

    assert result["page_count"] == 3
    assert result["page_count_source"] == "pillow"
    assert result["artifact_nature"] == "paginated_document"


def test_pdf_without_pymupdf_reports_unknown_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent PyMuPDF, dimensions must be null rather than an invented A4 size."""
    monkeypatch.setattr("acessilia_toolbox.providers.media_inspect.fitz", None)

    result = inspect(
        b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n", "doc.pdf", "application/pdf"
    )

    assert result["is_pdf"] is True
    assert result["dimensions"] is None
    assert result["dimensions_source"] is None
    assert result["page_count_source"] == "content_stream_scan"


def test_pdf_dimensions_are_measured_not_assumed() -> None:
    a4 = inspect(pdf_bytes(width=595, height=842), "a4.pdf", "application/pdf")
    letter = inspect(pdf_bytes(width=612, height=792), "letter.pdf", "application/pdf")

    assert a4["dimensions"] == {"width": 595, "height": 842, "aspect_ratio": 0.7067}
    assert letter["dimensions"] == {"width": 612, "height": 792, "aspect_ratio": 0.7727}
    assert a4["dimensions_source"] == "pymupdf"


def test_multi_page_pdf_reports_every_page() -> None:
    result = inspect(pdf_bytes(pages=3), "three.pdf", "application/pdf")

    assert result["page_count"] == 3
    assert result["page_count_source"] == "pymupdf"
    assert result["artifact_nature"] == "paginated_document"


def test_payload_without_pdf_structure_is_not_reported_as_a_pdf() -> None:
    """A declared PDF that carries no structure must not claim a page size."""
    result = inspect(b"garbage that is not a pdf", "fake.pdf", "application/pdf")

    assert result["is_pdf"] is False
    assert result["artifact_nature"] == "unknown"
    assert result["dimensions"] is None


def test_inspect_pdf_returns_none_for_unverifiable_payloads() -> None:
    assert _inspect_pdf(b"garbage", "fake.pdf", "application/pdf") is None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_portrait_page_sized_image_is_a_scanned_page() -> None:
    result = inspect(image_bytes(595, 842, mode="L"), "scanned_doc.png", "image/png")

    assert result["artifact_nature"] == "scanned_page"
    assert result["requires_visual_inspection"] is False


def test_landscape_page_sized_image_is_not_a_scanned_page() -> None:
    """A4 landscape is a document, not a page: its graphics may carry meaning."""
    result = inspect(image_bytes(842, 595, mode="L"), "landscape.png", "image/png")

    assert result["artifact_nature"] == "standalone_image"
    assert result["requires_visual_inspection"] is True


def test_small_images_are_not_scanned_pages() -> None:
    """A thumbnail shares the page aspect ratio but is not a page."""
    result = inspect(image_bytes(80, 120), "thumb.png", "image/png")

    assert result["artifact_nature"] == "standalone_image"
    assert result["requires_visual_inspection"] is True


def test_infographics_and_maps_require_visual_inspection() -> None:
    result = inspect(image_bytes(1920, 1080, fmt="JPEG"), "planisferio.jpg", "image/jpeg")

    assert result["artifact_nature"] == "standalone_image"
    assert result["requires_visual_inspection"] is True


# ---------------------------------------------------------------------------
# Request parameters
# ---------------------------------------------------------------------------


def test_min_dimension_parameter_raises_the_page_floor() -> None:
    payload = image_bytes(595, 842, mode="L")

    assert inspect(payload, "p.png", "image/png")["artifact_nature"] == "scanned_page"
    assert (
        inspect(payload, "p.png", "image/png", min_dimension_px=900)["artifact_nature"]
        == "standalone_image"
    )


def test_scanned_page_aspect_ratio_parameter_overrides_the_window() -> None:
    payload = image_bytes(842, 595, mode="L")

    result = inspect(payload, "l.png", "image/png", scanned_page_aspect_ratio=[0.6, 1.5])

    assert result["artifact_nature"] == "scanned_page"


def test_parameters_are_recorded_so_the_cache_key_can_differ() -> None:
    provider = PureMediaInspectProvider(descriptor())
    payload = image_bytes(595, 842, mode="L")

    result = provider.execute(
        CAPABILITY,
        payload,
        filename="p.png",
        media_type="image/png",
        parameters={"min_dimension_px": 900},
    )

    assert result.configuration["min_dimension_px"] == 900
    assert result.configuration["capability"] == CAPABILITY


@pytest.mark.parametrize("value", ["abc", 0, -5, 1.5])
def test_invalid_min_dimension_parameter_is_rejected(value: Any) -> None:
    with pytest.raises(InvalidInputError):
        _resolve_dimension({"min_dimension_px": value}, "min_dimension_px")


@pytest.mark.parametrize("value", [[0.8], [0.9, 0.5], "wide", [0.7, "high"]])
def test_invalid_aspect_ratio_parameter_is_rejected(value: Any) -> None:
    with pytest.raises(InvalidInputError):
        _resolve_aspect_ratio({"scanned_page_aspect_ratio": value}, "scanned_page_aspect_ratio")


def test_absent_parameters_fall_back_to_the_module_defaults() -> None:
    assert _resolve_dimension({}, "min_dimension_px") is None
    assert _resolve_aspect_ratio({}, "scanned_page_aspect_ratio") is None


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_output_validates_against_the_versioned_schema(
    schema: dict[str, Any],
) -> None:
    payloads = [
        (image_bytes(595, 842, mode="L"), "page.png", "image/png"),
        (image_bytes(1920, 1080, fmt="JPEG"), "map.jpg", "image/jpeg"),
        (image_bytes(600, 800, fmt="TIFF", frames=3), "multi.tiff", "image/tiff"),
        (b"unrecognized", "unknown.dat", "application/octet-stream"),
        (pdf_bytes(pages=2), "doc.pdf", "application/pdf"),
    ]

    for payload, filename, media_type in payloads:
        jsonschema.validate(instance=inspect(payload, filename, media_type), schema=schema)


def test_provider_executes_and_reports_health() -> None:
    provider = PureMediaInspectProvider(descriptor())
    health = provider.health()

    assert health.healthy is True
    assert health.provider == "pure-media-inspect"

    result = provider.execute(
        CAPABILITY,
        image_bytes(100, 100),
        filename="icon.png",
        media_type="image/png",
    )

    assert result.backend == "pure-python"
    assert result.document["is_image"] is True
    assert result.document["dimensions"]["width"] == 100
    assert result.document["artifact_nature"] == "standalone_image"


def test_provider_is_reachable_through_the_adapter_registry() -> None:
    adapter = create_adapter(descriptor())

    assert isinstance(adapter, PureMediaInspectProvider)
    assert adapter.versions()["provider"]


def test_provider_reports_unavailable_without_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter is constructed lazily, so a missing Pillow must not break startup."""
    monkeypatch.setattr("acessilia_toolbox.providers.media_inspect.Image", None)

    with pytest.raises(ProviderUnavailableError):
        PureMediaInspectProvider(descriptor())


def test_missing_pillow_is_reported_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("acessilia_toolbox.providers.media_inspect.Image", None)

    with pytest.raises(ProviderUnavailableError):
        _inspect_media(b"\x89PNG", "icon.png", "image/png")
