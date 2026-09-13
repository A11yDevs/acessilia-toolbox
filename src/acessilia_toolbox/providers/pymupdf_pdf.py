"""PyMuPDF provider adapter for PDF operations (split and render).

Thin adapter that uses PyMuPDF (fitz) directly for deterministic PDF
operations: splitting multi-page PDFs into single pages and rendering
pages to PNG images.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

try:
    import pymupdf as fitz  # type: ignore[import-untyped]
except ImportError:
    fitz = None


class PyMuPDFProvider:
    """Calls PyMuPDF directly for deterministic PDF operations."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        if fitz is None:
            raise ProviderUnavailableError(
                "PyMuPDF (fitz) is not installed. "
                "Install it with: pip install pymupdf",
                provider=descriptor.id,
            )

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        params = dict(parameters or {})

        if capability_id == "pdf.split":
            document = self._split(payload, filename, params)
        elif capability_id == "pdf.render":
            document = self._render(payload, filename, params)
        else:
            raise ProviderExecutionError(
                f"unknown capability {capability_id} for PyMuPDF provider",
                provider=self.descriptor.id,
            )

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=document,
            backend="pymupdf",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=self.descriptor.version,
            configuration={
                "provider": "pymupdf",
                "capability": capability_id,
                **params,
            },
        )

    def versions(self) -> dict[str, str]:
        if fitz is None:
            return {"provider": self.descriptor.version}
        return {
            "provider": self.descriptor.version,
            "pymupdf": str(fitz.version[0]),
        }

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        if fitz is None:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                detail="PyMuPDF not installed",
                checked_at=checked_at,
            )
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            version=str(fitz.version[0]),
            checked_at=checked_at,
        )

    def _split(
        self, payload: bytes, filename: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Split a multi-page PDF into individual pages."""
        max_pages = int(params.get("max_pages", 50))
        dpi = int(params.get("dpi", 150))

        pages: list[dict[str, Any]] = []
        total = 0
        doc = fitz.open(stream=payload, filetype="pdf")
        try:
            total = min(len(doc), max_pages)
            for i in range(total):
                page = doc[i]
                pix = page.get_pixmap(dpi=dpi)
                png_bytes = pix.tobytes("png")
                pages.append({
                    "page_number": i + 1,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "image_bytes_base64": _b64encode(png_bytes),
                    "size_bytes": len(png_bytes),
                })
        finally:
            doc.close()

        return {
            "pages": pages,
            "page_count": total,
            "source_filename": filename,
        }

    def _render(
        self, payload: bytes, filename: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Render a single PDF page to PNG."""
        dpi = int(params.get("dpi", 150))
        page_number = int(params.get("page_number", 1))

        width = height = 0.0
        png_bytes = b""
        doc = fitz.open(stream=payload, filetype="pdf")
        try:
            if page_number < 1 or page_number > len(doc):
                raise ProviderExecutionError(
                    f"page {page_number} out of range (1-{len(doc)})",
                    provider=self.descriptor.id,
                )
            page = doc[page_number - 1]
            width = page.rect.width
            height = page.rect.height
            pix = page.get_pixmap(dpi=dpi)
            png_bytes = pix.tobytes("png")
        finally:
            doc.close()

        return {
            "page_number": page_number,
            "width": width,
            "height": height,
            "image_bytes_base64": _b64encode(png_bytes),
            "size_bytes": len(png_bytes),
            "source_filename": filename,
        }


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
