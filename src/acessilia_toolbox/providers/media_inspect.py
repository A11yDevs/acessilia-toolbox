"""Pure-Python media inspection provider.

Inspects document and image payloads deterministically using Pillow and standard headers
to extract dimensions, page counts, MIME types, and determine whether the artifact is a
standalone visual image or a paginated/scanned document.
"""

from __future__ import annotations

import io
import mimetypes
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from PIL import Image

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

VERSION = "1.0.0"

fitz: Any
try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def _detect_pdf_page_count_and_size(payload: bytes) -> tuple[int, tuple[int, int] | None]:
    """Inspect PDF page count and first page dimensions without rendering."""
    if fitz is not None:
        try:
            doc = fitz.open(stream=payload, filetype="pdf")
            page_count = len(doc)
            dims = None
            if page_count > 0:
                page = doc[0]
                rect = page.rect
                dims = (round(rect.width), round(rect.height))
            return max(1, page_count), dims
        except Exception:
            pass

    # Fallback inspection via PDF header counting
    count = payload.count(b"/Type /Page") - payload.count(b"/Type /Pages")
    if count <= 0:
        count = 1
    return count, None


def _inspect_media(
    payload: bytes,
    filename: str,
    media_type: str,
) -> dict[str, Any]:
    """Perform deterministic inspection on the media payload."""
    size_bytes = len(payload)
    is_pdf = (
        media_type == "application/pdf"
        or filename.lower().endswith(".pdf")
        or payload.startswith(b"%PDF-")
    )

    if is_pdf:
        page_count, first_page_dims = _detect_pdf_page_count_and_size(payload)
        dims_dict = None
        if first_page_dims:
            w, h = first_page_dims
            aspect = round(w / h, 4) if h > 0 else 1.0
            dims_dict = {
                "width": w,
                "height": h,
                "aspect_ratio": aspect,
            }

        return {
            "media_type": "application/pdf",
            "file_size_bytes": size_bytes,
            "is_image": False,
            "is_pdf": True,
            "page_count": page_count,
            "dimensions": dims_dict or {"width": 595, "height": 842, "aspect_ratio": 0.7067},
            "format": "PDF",
            "artifact_nature": "paginated_document",
            "requires_visual_inspection": False,
            "metadata": {
                "filename": filename,
            },
        }

    # Attempt image inspection via Pillow
    try:
        with Image.open(io.BytesIO(payload)) as img:
            width, height = img.size
            format_name = img.format or "UNKNOWN"
            color_mode = img.mode
            aspect_ratio = round(width / height, 4) if height > 0 else 1.0

            # Heuristics:
            # Standard document pages (like A4 or US Letter) have aspect ratio around 0.65-0.78
            is_standard_page_aspect = 0.65 <= aspect_ratio <= 0.78
            nature = "scanned_page" if is_standard_page_aspect else "standalone_image"

            # Standalone images require visual inspection
            requires_visual = True

            guessed_type = mimetypes.guess_type(filename)[0] or f"image/{format_name.lower()}"

            return {
                "media_type": media_type if media_type.startswith("image/") else guessed_type,
                "file_size_bytes": size_bytes,
                "is_image": True,
                "is_pdf": False,
                "page_count": 1,
                "dimensions": {
                    "width": width,
                    "height": height,
                    "aspect_ratio": aspect_ratio,
                },
                "color_mode": color_mode,
                "format": format_name,
                "artifact_nature": nature,
                "requires_visual_inspection": requires_visual,
                "metadata": {
                    "filename": filename,
                },
            }
    except Exception:
        # Fallback for unrecognized media
        guessed_type = mimetypes.guess_type(filename)[0] or media_type or "application/octet-stream"
        return {
            "media_type": guessed_type,
            "file_size_bytes": size_bytes,
            "is_image": False,
            "is_pdf": False,
            "page_count": 1,
            "dimensions": {"width": 1, "height": 1, "aspect_ratio": 1.0},
            "artifact_nature": "unknown",
            "requires_visual_inspection": False,
            "metadata": {
                "filename": filename,
            },
        }


class PureMediaInspectProvider:
    """Pure-Python media and artifact inspection provider."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

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
        result = _inspect_media(payload, filename, media_type)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=result,
            backend="pure-python",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=VERSION,
            configuration={
                "capability": capability_id,
                **params,
            },
        )

    def versions(self) -> dict[str, str]:
        return {"provider": VERSION}

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            version=VERSION,
            checked_at=datetime.now(UTC),
        )


__all__ = ["PureMediaInspectProvider", "_inspect_media"]
