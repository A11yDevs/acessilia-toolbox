"""Pure-Python media inspection provider.

Inspects document and image payloads deterministically using PDF structure and
image container headers to extract dimensions, page counts and detected MIME
types, and to determine whether the artifact is a standalone visual image or a
paginated/scanned document.

Nothing is guessed: a value that cannot be measured is reported as ``null``
alongside the mechanism that produced it, so a consumer can always tell a
measured value from a missing one.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

VERSION = "1.1.0"

# Pillow is an optional dependency: the core install must keep working without
# it, so the import is guarded and the adapter reports itself unavailable.
Image: Any
UnidentifiedImageError: Any
try:
    from PIL import Image, UnidentifiedImageError
except ImportError:
    Image = None
    UnidentifiedImageError = None

# PyMuPDF is optional too, but only its absence degrades PDF metrics; the
# capability stays usable through content-stream page counting.
fitz: Any
try:
    import pymupdf as fitz
except ImportError:
    fitz = None

# Minimum edge length, in pixels, for an image to be considered a document page.
# Thumbnails and icons must not be classified as scanned pages.
MIN_SCANNED_PAGE_PX = 200

# A4 and US Letter portrait pages fall between these width/height ratios.
SCANNED_PAGE_ASPECT = (0.65, 0.78)

# Producers and creators that only make slide decks, matched as whole words so
# "Impressions" is not "Impress". Google Slides is absent: its PDFs carry the
# same "Google" producer as Google Docs, so geometry decides.
PRESENTATION_SOFTWARE = re.compile(r"\b(powerpoint|keynote|impress|beamer)\b", re.IGNORECASE)

# Slide shapes: 4:3, 16:10 and 16:9. Only a narrow tolerance around each is
# accepted, so landscape paper (Letter 1.29, A4 1.41, Legal 1.65) is not a deck.
PRESENTATION_ASPECTS = (4 / 3, 16 / 10, 16 / 9)
PRESENTATION_ASPECT_TOLERANCE = 0.02

# Share of pages with the deck's own page size: a document with a couple of
# landscape tables among portrait pages is not a presentation.
PRESENTATION_UNIFORM_SHARE = 0.9

# Container format -> MIME type. Pillow's own ``Image.MIME`` map omits formats
# that are not built into the running binary, so the mapping is kept explicit.
MEDIA_TYPE_BY_FORMAT = {
    "BMP": "image/bmp",
    "GIF": "image/gif",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "TIFF": "image/tiff",
    "WEBP": "image/webp",
}


def _resolve_dimension(params: Mapping[str, Any], key: str) -> int | None:
    """Read a positive pixel threshold from the request parameters."""
    value = params.get(key)
    if value is None:
        return None
    # Reject bools and floats: silently truncating 1.5 to 1 would accept a
    # value the caller never asked for.
    if isinstance(value, (bool, float)) or not isinstance(value, (int, str)):
        raise InvalidInputError(
            f"parameter {key!r} must be an integer, got {value!r}"
        )
    try:
        parsed = int(value)
    except ValueError as exc:
        raise InvalidInputError(
            f"parameter {key!r} must be an integer, got {value!r}"
        ) from exc
    if parsed < 1:
        raise InvalidInputError(f"parameter {key!r} must be >= 1, got {parsed}")
    return parsed


def _resolve_aspect_ratio(params: Mapping[str, Any], key: str) -> tuple[float, float] | None:
    """Read the scanned-page aspect-ratio window from the request parameters."""
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise InvalidInputError(
            f"parameter {key!r} must be a [low, high] pair, got {value!r}"
        )
    try:
        low, high = (float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(
            f"parameter {key!r} must contain numbers, got {value!r}"
        ) from exc
    if not 0 < low < high:
        raise InvalidInputError(
            f"parameter {key!r} must satisfy 0 < low < high, got {value!r}"
        )
    return low, high


def _dimensions(width: int, height: int) -> dict[str, Any]:
    """Pixel dimensions plus the derived aspect ratio."""
    return {
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 4),
    }


def _pdf_page_count_from_content(payload: bytes) -> int:
    """Count page objects by scanning the document content stream.

    Last resort: PDFs that store their page tree inside object streams — the
    default since PDF 1.5 — report a single page.
    """
    counted = payload.count(b"/Type /Page") - payload.count(b"/Type /Pages")
    return max(1, counted)


def _document_genre(
    page_sizes: list[tuple[int, int]], metadata: Mapping[str, Any]
) -> tuple[str, list[str]]:
    """Whether a PDF is a slide deck, and the signals that say so.

    Either signal suffices: software that only makes decks, or two or more
    pages that are nearly all the same 4:3, 16:10 or 16:9 landscape size.
    """
    # The token is lowercased so consumers can compare it as is, whatever the
    # casing the metadata used ("Microsoft® POWERPOINT®" -> producer:powerpoint).
    signals = [
        f"{field}:{match.group(1).lower()}"
        for field in ("producer", "creator")
        if (match := PRESENTATION_SOFTWARE.search(str(metadata.get(field) or "")))
    ]
    if len(page_sizes) >= 2:
        size, count = Counter(page_sizes).most_common(1)[0]
        width, height = size
        if (
            count / len(page_sizes) >= PRESENTATION_UNIFORM_SHARE
            and height > 0
            and any(
                abs(width / height - aspect) <= PRESENTATION_ASPECT_TOLERANCE
                for aspect in PRESENTATION_ASPECTS
            )
        ):
            signals.append("geometry:landscape-uniform")
    return ("presentation" if signals else "document"), signals


def _inspect_pdf(payload: bytes, filename: str, media_type: str) -> dict[str, Any] | None:
    """Inspect a PDF, using PyMuPDF when available.

    Returns ``None`` when the payload carries no verifiable PDF structure, so
    the caller reports it as unrecognized instead of claiming a page size that
    was never measured.
    """
    page_count = _pdf_page_count_from_content(payload)
    page_count_source: str | None = "content_stream_scan"
    dimensions: dict[str, Any] | None = None
    dimensions_source: str | None = None
    # Without PyMuPDF neither the pages nor the metadata are measured, so the
    # genre stays unknown instead of defaulting to "document".
    genre: str | None = None
    genre_signals: list[str] = []
    verified = payload.startswith(b"%PDF-")

    if fitz is not None:
        document = None
        try:
            document = fitz.open(stream=payload, filetype="pdf")
            opened_pages = len(document)
            if opened_pages > 0:
                verified = True
                page_count = opened_pages
                page_count_source = "pymupdf"
                frame = document[0].rect
                width, height = round(frame.width), round(frame.height)
                if width > 0 and height > 0:
                    dimensions = _dimensions(width, height)
                    dimensions_source = "pymupdf"
                # The genre is optional, so a damaged later page or unreadable
                # metadata leaves it unknown instead of failing an inspection
                # that page 0 already answered.
                try:
                    genre, genre_signals = _document_genre(
                        [(round(page.rect.width), round(page.rect.height)) for page in document],
                        document.metadata or {},
                    )
                except Exception:
                    genre, genre_signals = None, []
        except fitz.FileDataError:
            pass
        finally:
            if document is not None:
                document.close()

    if not verified:
        return None

    return {
        "media_type": "application/pdf",
        "declared_media_type": media_type or None,
        "file_size_bytes": len(payload),
        "is_image": False,
        "is_pdf": True,
        "page_count": page_count,
        "page_count_source": page_count_source,
        "dimensions": dimensions,
        "dimensions_source": dimensions_source,
        "color_mode": None,
        "format": "PDF",
        "artifact_nature": "paginated_document",
        "document_genre": genre,
        "genre_signals": genre_signals,
        "requires_visual_inspection": False,
        "metadata": {"filename": filename},
    }


def _unsupported_media(payload: bytes, filename: str, media_type: str) -> dict[str, Any]:
    """Report an artifact whose container could not be identified."""
    return {
        # Nothing was detected, so the neutral type is reported and the
        # caller's declaration is preserved separately.
        "media_type": "application/octet-stream",
        "declared_media_type": media_type or None,
        "file_size_bytes": len(payload),
        "is_image": False,
        "is_pdf": False,
        "page_count": 1,
        "page_count_source": None,
        "dimensions": None,
        "dimensions_source": None,
        "color_mode": None,
        "format": None,
        "artifact_nature": "unknown",
        "document_genre": None,
        "genre_signals": [],
        "requires_visual_inspection": False,
        "metadata": {"filename": filename},
    }


def _inspect_media(
    payload: bytes,
    filename: str,
    media_type: str,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform deterministic inspection on the media payload."""
    params = dict(parameters or {})
    size_bytes = len(payload)
    is_pdf_signal = (
        media_type == "application/pdf"
        or filename.lower().endswith(".pdf")
        or payload.startswith(b"%PDF-")
    )
    if is_pdf_signal:
        document = _inspect_pdf(payload, filename, media_type)
        if document is not None:
            return document
        return _unsupported_media(payload, filename, media_type)

    if Image is None:
        raise ProviderUnavailableError(
            "Pillow is not installed, so image payloads cannot be inspected. "
            "Install it with: pip install 'acessilia-toolbox[media]'",
            provider="pure-media-inspect",
        )

    min_dimension = _resolve_dimension(params, "min_dimension_px")
    if min_dimension is None:
        min_dimension = MIN_SCANNED_PAGE_PX
    aspect_window = _resolve_aspect_ratio(params, "scanned_page_aspect_ratio")
    if aspect_window is None:
        aspect_window = SCANNED_PAGE_ASPECT

    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            if width < 1 or height < 1:
                raise ProviderExecutionError(
                    f"image reports invalid dimensions: {width}x{height}",
                    provider="pure-media-inspect",
                )
            # Multi-frame containers (TIFF, GIF, WebP, APNG) are paginated
            # documents; Pillow exposes the count through n_frames.
            frame_count = max(1, int(getattr(image, "n_frames", 1)))
            format_name = image.format or "UNKNOWN"
            color_mode = image.mode
            detected_type = MEDIA_TYPE_BY_FORMAT.get(
                format_name, f"image/{format_name.lower()}"
            )
    except UnidentifiedImageError:
        return _unsupported_media(payload, filename, media_type)

    aspect_ratio = round(width / height, 4)
    is_page_sized = min(width, height) >= min_dimension
    if frame_count > 1:
        nature = "paginated_document"
    elif is_page_sized and aspect_window[0] <= aspect_ratio <= aspect_window[1]:
        nature = "scanned_page"
    else:
        nature = "standalone_image"

    return {
        # Detected from the container, never taken from the filename or the
        # caller's declaration.
        "media_type": detected_type,
        "declared_media_type": media_type or None,
        "file_size_bytes": size_bytes,
        "is_image": True,
        "is_pdf": False,
        "page_count": frame_count,
        "page_count_source": "pillow",
        "dimensions": _dimensions(width, height),
        "dimensions_source": "pillow",
        "color_mode": color_mode,
        "format": format_name,
        "artifact_nature": nature,
        # A slide deck is told apart by its pages; a single image has none.
        "document_genre": None,
        "genre_signals": [],
        # Only a page-sized, single-frame, document-proportioned image is
        # assumed to be text-recoverable; everything else may carry meaning in
        # its graphics, so vision inspection is requested.
        "requires_visual_inspection": nature != "scanned_page",
        "metadata": {"filename": filename},
    }


class PureMediaInspectProvider:
    """Pure-Python media and artifact inspection provider."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        if Image is None:
            raise ProviderUnavailableError(
                "Pillow is not installed. "
                "Install it with: pip install 'acessilia-toolbox[media]'",
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
        result = _inspect_media(payload, filename, media_type, params)

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


__all__ = [
    "PureMediaInspectProvider",
    "_dimensions",
    "_inspect_media",
    "_inspect_pdf",
    "_resolve_aspect_ratio",
    "_resolve_dimension",
]
