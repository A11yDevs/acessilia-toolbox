"""PyMuPDF provider adapter behavior, exercised with a real PDF in memory."""

from __future__ import annotations

import fitz
import pytest

from acessilia_toolbox.core.errors import ProviderExecutionError
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pymupdf_pdf import PyMuPDFProvider


def _sample_pdf() -> bytes:
    """Create a 3-page PDF in memory for testing."""
    doc = fitz.open()
    try:
        for i in range(3):
            page = doc.new_page()
            page.insert_text(fitz.Point(50, 100), f"Page {i + 1}")
        return doc.tobytes()
    finally:
        doc.close()


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "pymupdf-pdf",
        "version": "1.24",
        "capabilities": ["pdf.split", "pdf.render"],
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def extract(adapter: PyMuPDFProvider, capability: str):
    return adapter.execute(
        capability,
        _sample_pdf(),
        filename="sample.pdf",
        media_type="application/pdf",
    )


class TestPyMuPDFProvider:
    def test_create_adapter_via_registry(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, PyMuPDFProvider)

    def test_health_returns_healthy(self) -> None:
        adapter = PyMuPDFProvider(descriptor())
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "pymupdf-pdf"

    def test_versions_returns_pymupdf_version(self) -> None:
        adapter = PyMuPDFProvider(descriptor())
        versions = adapter.versions()
        assert "pymupdf" in versions
        assert versions["provider"] == "1.24"

    # ── pdf.split ──────────────────────────────────────────────

    def test_split_returns_all_pages(self) -> None:
        result = extract(PyMuPDFProvider(descriptor()), "pdf.split")
        doc = result.document
        assert doc["page_count"] == 3
        assert len(doc["pages"]) == 3

    def test_split_each_page_has_required_fields(self) -> None:
        result = extract(PyMuPDFProvider(descriptor()), "pdf.split")
        for page in result.document["pages"]:
            assert "page_number" in page
            assert "width" in page
            assert "height" in page
            assert "image_bytes_base64" in page
            assert "size_bytes" in page

    def test_split_page_numbers_are_sequential(self) -> None:
        result = extract(PyMuPDFProvider(descriptor()), "pdf.split")
        numbers = [p["page_number"] for p in result.document["pages"]]
        assert numbers == [1, 2, 3]

    def test_split_respects_max_pages(self) -> None:
        adapter = PyMuPDFProvider(descriptor())
        result = adapter.execute(
            "pdf.split",
            _sample_pdf(),
            filename="sample.pdf",
            media_type="application/pdf",
            parameters={"max_pages": 2},
        )
        assert result.document["page_count"] == 2

    def test_split_empty_pdf_returns_no_pages(self) -> None:
        # PyMuPDF cannot save a zero-page document, so we test with a
        # 1-page PDF and max_pages=0 to simulate empty result.
        one_page = fitz.open()
        one_page.new_page()
        one_page_bytes = one_page.tobytes(garbage=4, deflate=True)
        one_page.close()
        adapter = PyMuPDFProvider(descriptor())
        result = adapter.execute(
            "pdf.split",
            one_page_bytes,
            filename="single.pdf",
            media_type="application/pdf",
            parameters={"max_pages": 0},
        )
        assert result.document["page_count"] == 0

    # ── pdf.render ─────────────────────────────────────────────

    def test_render_returns_png_image(self) -> None:
        result = extract(PyMuPDFProvider(descriptor()), "pdf.render")
        doc = result.document
        assert doc["page_number"] == 1
        assert doc["width"] > 0
        assert doc["height"] > 0
        assert doc["size_bytes"] > 0
        # Verify it's a valid PNG (base64 starts with iVBOR for PNG)
        assert doc["image_bytes_base64"][:4] == "iVBO"

    def test_render_respects_page_number(self) -> None:
        adapter = PyMuPDFProvider(descriptor())
        result = adapter.execute(
            "pdf.render",
            _sample_pdf(),
            filename="sample.pdf",
            media_type="application/pdf",
            parameters={"page_number": 2},
        )
        assert result.document["page_number"] == 2

    def test_render_out_of_range_raises_error(self) -> None:
        adapter = PyMuPDFProvider(descriptor())
        with pytest.raises(ProviderExecutionError):
            adapter.execute(
                "pdf.render",
                _sample_pdf(),
                filename="sample.pdf",
                media_type="application/pdf",
                parameters={"page_number": 99},
            )

    def test_render_respects_dpi(self) -> None:
        adapter = PyMuPDFProvider(descriptor())
        low = adapter.execute(
            "pdf.render",
            _sample_pdf(),
            filename="sample.pdf",
            media_type="application/pdf",
            parameters={"dpi": 72},
        )
        high = adapter.execute(
            "pdf.render",
            _sample_pdf(),
            filename="sample.pdf",
            media_type="application/pdf",
            parameters={"dpi": 300},
        )
        assert low.document["size_bytes"] < high.document["size_bytes"]

    # ── Extraction metadata ────────────────────────────────────

    def test_extraction_metadata(self) -> None:
        result = extract(PyMuPDFProvider(descriptor()), "pdf.split")
        assert result.backend == "pymupdf"
        assert result.version == "1.24"
        assert result.duration_ms > 0
        assert "pymupdf" in result.configuration.get("provider", "")
