"""pdf.render@2: clipped and bounded renders of PDF, PNG and JPEG pages, by reference."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import fitz
import pytest

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError, UnsupportedMediaTypeError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.pymupdf_pdf import MAX_RENDER_PIXELS, PyMuPDFProvider
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _pdf(pages: int = 2, width: float = 200, height: float = 100) -> bytes:
    doc = fitz.open()
    try:
        for _ in range(pages):
            doc.new_page(width=width, height=height)
        return doc.tobytes()
    finally:
        doc.close()


def _image(filetype: str) -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page(width=200, height=100)
        # 72 dpi: one page unit per pixel, so the image page is 200 x 100.
        return page.get_pixmap(dpi=72).tobytes(filetype)
    finally:
        doc.close()


def _descriptor() -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {"id": "pymupdf-pdf", "version": "1.24", "capabilities": ["pdf.split", "pdf.render"]}
    )


def render(payload: bytes, media_type: str = "application/pdf", **params):
    result = PyMuPDFProvider(_descriptor()).execute(
        "pdf.render",
        payload,
        filename="doc.pdf",
        media_type=media_type,
        parameters=params,
        capability_version=2,
    )
    [output] = result.outputs
    return result.document, output


def _png_size(png: bytes) -> tuple[int, int]:
    pix = fitz.Pixmap(png)
    return pix.width, pix.height


# ── Provider ────────────────────────────────────────────────────────────


def test_full_page_is_returned_as_a_binary_output_with_its_transformation():
    document, output = render(_pdf(), page_number=2, dpi=144)

    assert output.media_type == "image/png" and output.payload.startswith(b"\x89PNG")
    assert document["artifact_id"].endswith(
        __import__("hashlib").sha256(output.payload).hexdigest()
    )
    assert document["size_bytes"] == len(output.payload)
    assert (document["page_number"], document["page_count"]) == (2, 2)
    assert (document["page_width"], document["page_height"]) == (200, 100)
    assert document["clip"] == {"left": 0, "top": 0, "right": 200, "bottom": 100}
    assert document["scale"] == 2 and document["dpi"] == 144
    assert (document["width_px"], document["height_px"]) == (400, 200)
    assert _png_size(output.payload) == (400, 200)
    assert "image_bytes_base64" not in document


def test_max_side_bounds_the_longest_side_and_the_smaller_scale_wins():
    document, _ = render(_pdf(), dpi=600, max_side=100)
    assert (document["width_px"], document["height_px"]) == (100, 50)
    assert document["scale"] == pytest.approx(0.5)

    document, _ = render(_pdf(), dpi=72, max_side=10_000)
    assert document["scale"] == 1


@pytest.mark.parametrize(
    ("clip", "expected"),
    [
        ({"left": 10, "top": 20, "right": 60, "bottom": 40}, (10, 20, 60, 40)),
        # A bottom-left box 20..40 from the bottom is 60..80 from the top.
        (
            {"left": 10, "top": 40, "right": 60, "bottom": 20, "origin": "BOTTOMLEFT"},
            (10, 60, 60, 80),
        ),
        # Swapped corners are normalized; the part outside the page is cut.
        ({"left": 250, "top": 50, "right": 150, "bottom": 0}, (150, 0, 200, 50)),
    ],
)
def test_clip_is_converted_normalized_and_intersected(clip, expected):
    document, output = render(_pdf(), dpi=72, clip=clip)

    applied = document["clip"]
    assert (applied["left"], applied["top"], applied["right"], applied["bottom"]) == expected
    width, height = expected[2] - expected[0], expected[3] - expected[1]
    assert _png_size(output.payload) == (width, height)


@pytest.mark.parametrize(
    ("clip", "message"),
    [
        ({"left": 300, "top": 0, "right": 400, "bottom": 50}, "does not intersect"),
        ({"left": 0, "top": 0, "right": 50}, "numeric"),
        ({"left": 0, "top": 0, "right": "x", "bottom": 50}, "numeric"),
        ({"left": 0, "top": 0, "right": float("inf"), "bottom": 50}, "finite"),
        ({"left": 0, "top": 0, "right": 50, "bottom": 50, "origin": "CENTER"}, "origin"),
        ({"left": 0, "top": 0, "right": 50, "bottom": 50, "unit": "px"}, "unknown clip"),
        ("0,0,50,50", "object"),
    ],
)
def test_invalid_clips_are_refused(clip, message):
    with pytest.raises(InvalidInputError, match=message):
        render(_pdf(), clip=clip)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"page_number": 3}, "out of range"),
        ({"page_number": 0}, "page_number"),
        ({"dpi": 601}, "dpi"),
        ({"max_side": 8}, "max_side"),
        ({"max_side": 2.5}, "max_side"),
    ],
)
def test_invalid_parameters_are_refused(params, message):
    with pytest.raises(InvalidInputError, match=message):
        render(_pdf(), **params)


def test_renders_above_the_pixel_limit_are_refused():
    big = _pdf(pages=1, width=14_400, height=14_400)
    with pytest.raises(InvalidInputError, match=str(MAX_RENDER_PIXELS)):
        render(big, dpi=72)
    # max_side brings the same page within the limit.
    document, _ = render(big, dpi=72, max_side=1_000)
    assert document["width_px"] == 1_000


@pytest.mark.parametrize(("filetype", "media_type"), [("png", "image/png"), ("jpeg", "image/jpeg")])
def test_raster_inputs_are_their_own_single_page(filetype, media_type):
    document, output = render(_image(filetype), media_type, dpi=72)

    assert document["page_count"] == 1
    assert (document["page_width"], document["page_height"]) == (200, 100)
    assert document["source_media_type"] == media_type
    assert _png_size(output.payload) == (200, 100)
    with pytest.raises(InvalidInputError, match="out of range"):
        render(_image(filetype), media_type, page_number=2)


def test_an_unreadable_input_is_an_invalid_input():
    with pytest.raises(InvalidInputError, match="cannot open"):
        render(b"not an image", "image/png")


def test_version_1_is_unchanged():
    result = PyMuPDFProvider(_descriptor()).execute(
        "pdf.render",
        _pdf(),
        filename="doc.pdf",
        media_type="application/pdf",
        parameters={"page_number": 1, "dpi": 72},
    )

    assert result.outputs == ()
    assert base64.b64decode(result.document["image_bytes_base64"]).startswith(b"\x89PNG")


# ── Executor and catalogue ──────────────────────────────────────────────


def _executor(tmp_path: Path) -> tuple[CapabilityExecutor, FilesystemArtifactStore]:
    adapter = PyMuPDFProvider(_descriptor())
    store = FilesystemArtifactStore(tmp_path)
    executor = CapabilityExecutor(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"),
        ProviderRegistry([adapter.descriptor]),
        lambda _: adapter,
        store=store,
    )
    return executor, store


def test_version_2_is_the_default_and_stores_the_png(tmp_path):
    executor, store = _executor(tmp_path)

    result = executor.execute(
        "pdf.render",
        _pdf(),
        filename="doc.pdf",
        media_type="application/pdf",
        parameters={"page_number": 1, "dpi": 72},
    )

    assert result.provenance.capability_version == 2
    [png_ref] = [ref for ref in result.artifacts if ref.media_type == "image/png"]
    assert png_ref.artifact_id == result.document["artifact_id"]
    assert store.get(png_ref.artifact_id).startswith(b"\x89PNG")
    # The bytes travel by reference only.
    assert "image_bytes_base64" not in json.dumps(result.model_dump(mode="json"))


def test_version_1_stays_executable_by_explicit_version(tmp_path):
    executor, _ = _executor(tmp_path)

    result = executor.execute(
        "pdf.render",
        _pdf(),
        filename="doc.pdf",
        media_type="application/pdf",
        capability_version=1,
        parameters={"page_number": 1, "dpi": 72},
    )

    assert result.provenance.capability_version == 1
    assert "image_bytes_base64" in result.document


def test_version_1_still_refuses_raster_inputs(tmp_path):
    executor, _ = _executor(tmp_path)

    with pytest.raises(UnsupportedMediaTypeError):
        executor.execute(
            "pdf.render",
            _image("png"),
            filename="page.png",
            media_type="image/png",
            capability_version=1,
        )


def test_registry_publishes_both_versions_but_one_latest():
    registry = CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities")

    assert [m.version for m in registry.manifests() if m.id == "pdf.render"] == [1, 2]
    [latest] = [m for m in registry.latest() if m.id == "pdf.render"]
    assert latest.version == 2 and latest.output.by_reference
    assert len(registry.latest()) == len(registry.ids())
