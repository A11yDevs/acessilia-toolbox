"""PyMuPDF provider adapter for PDF operations (split and render).

Thin adapter that uses PyMuPDF (fitz) directly for deterministic PDF
operations: splitting multi-page PDFs into single pages and rendering
pages to PNG images.

``pdf.render`` has two contracts. Version 1 renders a PDF page at a DPI and
returns it inline as base64. Version 2 also takes PNG and JPEG inputs, an
optional ``clip`` region and a ``max_side`` bound, and returns the PNG as a
binary output that the executor stores as an artifact, together with the
transformation actually applied (see docs/pdf-render.md).
"""

from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import BinaryOutput, ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

fitz: Any
try:
    import pymupdf as fitz
except ImportError:
    fitz = None


#: pdf.render@2 refuses renders above this many pixels (width times height).
MAX_RENDER_PIXELS = 20_000_000
#: Bounds of the pdf.render@2 ``max_side`` parameter, in pixels.
MAX_SIDE_RANGE = (16, 10_000)
#: Input media types pdf.render@2 opens, mapped to the PyMuPDF file type.
RENDER_FILETYPES = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg"}
CLIP_ORIGINS = {"TOPLEFT", "BOTTOMLEFT"}


def _positive_integer(params: dict[str, Any], name: str, default: int, maximum: int) -> int:
    value = params.get(name, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise InvalidInputError(f"{name} must be an integer between 1 and {maximum}")
    return value


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
        capability_version: int = 1,
    ) -> ExtractionResult:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        params = dict(parameters or {})
        outputs: tuple[BinaryOutput, ...] = ()

        if capability_id == "pdf.split":
            document = self._split(payload, filename, params)
        elif capability_id == "pdf.render" and capability_version == 2:
            document, png = self._render_v2(payload, filename, media_type, params)
            stem = filename.rsplit(".", 1)[0] or "document"
            outputs = (
                BinaryOutput(png, "image/png", f"{stem}.page-{document['page_number']}.png"),
            )
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
            outputs=outputs,
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
        max_pages = _positive_integer(params, "max_pages", 50, 1000)
        dpi = _positive_integer(params, "dpi", 150, 600)

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
        dpi = _positive_integer(params, "dpi", 150, 600)
        page_number = _positive_integer(params, "page_number", 1, 1000000)

        width = height = 0.0
        png_bytes = b""
        doc = fitz.open(stream=payload, filetype="pdf")
        try:
            if page_number < 1 or page_number > len(doc):
                raise InvalidInputError(
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


    def _render_v2(
        self, payload: bytes, filename: str, media_type: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], bytes]:
        """pdf.render@2: one page, optionally clipped and bounded, as a stored PNG."""
        filetype = RENDER_FILETYPES.get(media_type)
        if filetype is None:
            raise InvalidInputError(
                f"pdf.render@2 cannot open {media_type}", provider=self.descriptor.id
            )
        page_number = _positive_integer(params, "page_number", 1, 1_000_000)
        dpi = _positive_integer(params, "dpi", 150, 600)
        max_side = params.get("max_side")
        if max_side is not None and (
            type(max_side) is not int or not MAX_SIDE_RANGE[0] <= max_side <= MAX_SIDE_RANGE[1]
        ):
            raise InvalidInputError(
                f"max_side must be an integer between {MAX_SIDE_RANGE[0]} and {MAX_SIDE_RANGE[1]}",
                provider=self.descriptor.id,
            )

        try:
            doc = fitz.open(stream=payload, filetype=filetype)
        except Exception as error:
            raise InvalidInputError(
                f"cannot open the input as {media_type}: {error}", provider=self.descriptor.id
            ) from error
        try:
            page_count = len(doc)
            if not 1 <= page_number <= page_count:
                raise InvalidInputError(
                    f"page {page_number} out of range (1-{page_count})",
                    provider=self.descriptor.id,
                )
            try:
                # Raster formats are decoded lazily: a corrupt image fails here.
                page = doc[page_number - 1]
                page_rect = page.rect
            except Exception as error:
                raise InvalidInputError(
                    f"cannot open the input as {media_type}: {error}",
                    provider=self.descriptor.id,
                ) from error
            rect = _clip_rect(page_rect, params.get("clip"), self.descriptor.id)
            scale = dpi / 72
            if max_side is not None:
                scale = min(scale, max_side / max(rect.width, rect.height))
            if rect.width * scale * rect.height * scale > MAX_RENDER_PIXELS:
                raise InvalidInputError(
                    f"render exceeds {MAX_RENDER_PIXELS} pixels; lower dpi or set max_side",
                    provider=self.descriptor.id,
                )
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
            png = pix.tobytes("png")
            document = {
                "artifact_id": fingerprint_bytes(png),
                "media_type": "image/png",
                "size_bytes": len(png),
                "page_number": page_number,
                "page_count": page_count,
                "page_width": page_rect.width,
                "page_height": page_rect.height,
                "clip": {
                    "left": rect.x0,
                    "top": rect.y0,
                    "right": rect.x1,
                    "bottom": rect.y1,
                },
                "scale": scale,
                "dpi": scale * 72,
                "width_px": pix.width,
                "height_px": pix.height,
                "source_media_type": media_type,
                "source_filename": filename,
            }
        finally:
            doc.close()
        return document, png


def _clip_rect(page_rect: Any, clip: Any, provider: str) -> Any:
    """The region to render, in top-left page units, intersected with the page."""
    if clip is None:
        return page_rect
    if not isinstance(clip, dict):
        raise InvalidInputError("clip must be an object", provider=provider)
    unknown = set(clip) - {"left", "top", "right", "bottom", "origin"}
    if unknown:
        raise InvalidInputError(f"unknown clip fields: {sorted(unknown)}", provider=provider)
    origin = clip.get("origin", "TOPLEFT")
    if origin not in CLIP_ORIGINS:
        raise InvalidInputError(
            f"clip.origin must be one of {sorted(CLIP_ORIGINS)}", provider=provider
        )
    try:
        left, top, right, bottom = (float(clip[key]) for key in ("left", "top", "right", "bottom"))
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidInputError(
            "clip needs numeric left, top, right and bottom", provider=provider
        ) from error
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        raise InvalidInputError("clip coordinates must be finite", provider=provider)
    if origin == "BOTTOMLEFT":
        top, bottom = page_rect.height - top, page_rect.height - bottom
    rect = fitz.Rect(
        min(left, right), min(top, bottom), max(left, right), max(top, bottom)
    ) & page_rect
    if rect.is_empty or rect.width < 1 or rect.height < 1:
        raise InvalidInputError("clip does not intersect the page", provider=provider)
    return rect


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
