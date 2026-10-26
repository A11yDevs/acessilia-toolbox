"""document.text.spans@1: the text layer as lines of spans with font and geometry."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import jsonschema
import pytest

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError, UnsupportedMediaTypeError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.pymupdf_pdf import PyMuPDFProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((PROJECT_ROOT / "schemas" / "text-spans@1.json").read_text())


def _pdf(pages: int = 1, rotation: int = 0) -> bytes:
    """Pages with a body-size "s", a lowered small "0", a word and a blank line."""
    doc = fitz.open()
    try:
        for _ in range(pages):
            page = doc.new_page(width=200, height=100)
            page.insert_text((10, 50), "s", fontsize=12, fontname="Times-Italic")
            page.insert_text((16, 52), "0", fontsize=8, fontname="Helvetica")
            page.insert_text((50, 50), "texto", fontsize=12, fontname="Times-Roman")
            page.insert_text((10, 80), "   ", fontsize=12, fontname="Courier")
            page.set_rotation(rotation)
        return doc.tobytes()
    finally:
        doc.close()


def _descriptor() -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "pymupdf-pdf",
            "version": "1.24",
            "capabilities": ["pdf.split", "pdf.render", "document.text.spans"],
        }
    )


def spans(payload: bytes, **params):
    return PyMuPDFProvider(_descriptor()).execute(
        "document.text.spans",
        payload,
        filename="doc.pdf",
        media_type="application/pdf",
        parameters=params,
    ).document


def _all_spans(document):
    return [span for page in document["pages"] for line in page["lines"] for span in line["spans"]]


# ── Provider ────────────────────────────────────────────────────────────


def test_spans_carry_font_size_and_baseline():
    document = spans(_pdf())

    jsonschema.validate(document, SCHEMA)
    assert document["page_count"] == 1 and document["coord_origin"] == "TOPLEFT"
    by_text = {span["text"]: span for span in _all_spans(document)}
    assert by_text["s"]["font"] == "Times-Italic" and by_text["s"]["size"] == 12
    assert by_text["0"]["size"] == 8
    # The lowered script sits on a baseline below the body's (y grows downwards).
    assert by_text["0"]["origin"][1] > by_text["s"]["origin"][1]
    assert by_text["s"]["bbox"][0] == pytest.approx(10, abs=0.5)


def test_whitespace_spans_are_skipped():
    assert all(span["text"].strip() for span in _all_spans(spans(_pdf())))


def test_pages_selects_and_orders_the_pages():
    document = spans(_pdf(pages=3), pages=[3, 1, 3])

    assert document["page_count"] == 3
    assert [page["page_number"] for page in document["pages"]] == [1, 3]


def test_a_rotated_page_keeps_the_unrotated_space():
    [page] = spans(_pdf(rotation=90))["pages"]

    assert page["rotation"] == 90
    assert (page["width"], page["height"]) == (200, 100)


def test_a_synthesized_space_does_not_move_the_origin():
    """PyMuPDF prefixes the "0" with a space on the body's baseline; the span
    must report the digit's own baseline and box."""
    by_text = {span["text"]: span for span in _all_spans(spans(_pdf()))}

    assert by_text["0"]["origin"] == [16, 52]
    assert by_text["0"]["bbox"][0] == 16


def test_subset_prefix_is_removed():
    from acessilia_toolbox.providers.pymupdf_pdf import _span

    char = {"c": "t", "origin": (1, 3.5), "bbox": (1, 2, 3, 4)}
    span = {"font": "ABCDEF+CMMI12", "size": 11.955, "flags": 6, "chars": [char]}

    assert _span(span)["font"] == "CMMI12"
    assert _span(span)["size"] == 11.96


@pytest.mark.parametrize("pages", [[], [0], [2], ["1"], "1", [True]])
def test_invalid_pages_are_refused(pages):
    with pytest.raises(InvalidInputError):
        spans(_pdf(), pages=pages)


def test_a_long_document_must_name_its_pages(monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.providers.pymupdf_pdf.MAX_SPAN_PAGES", 2)

    with pytest.raises(InvalidInputError, match="pages parameter"):
        spans(_pdf(pages=3))
    assert len(spans(_pdf(pages=3), pages=[1, 2])["pages"]) == 2


# ── Executor and catalogue ──────────────────────────────────────────────


def _executor() -> CapabilityExecutor:
    adapter = PyMuPDFProvider(_descriptor())
    return CapabilityExecutor(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"),
        ProviderRegistry([adapter.descriptor]),
        lambda _: adapter,
    )


def test_the_executor_runs_the_capability():
    result = _executor().execute(
        "document.text.spans", _pdf(), filename="doc.pdf", media_type="application/pdf"
    )

    assert result.provenance.capability_version == 1
    jsonschema.validate(result.document, SCHEMA)


def test_the_executor_refuses_an_image():
    with pytest.raises(UnsupportedMediaTypeError):
        _executor().execute(
            "document.text.spans", b"\x89PNG", filename="page.png", media_type="image/png"
        )


def test_the_manifest_is_wired_to_pymupdf():
    manifest = CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities").get(
        "document.text.spans"
    )

    assert manifest.output.schema_ref == "artifact/text-spans@1"
    assert [binding.id for binding in manifest.providers] == ["pymupdf-pdf"]
    assert manifest.execution.deterministic
