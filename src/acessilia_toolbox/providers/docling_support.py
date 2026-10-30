"""Shared support utilities for Docling providers.

Centralizes coordinate normalization and page geometry extraction for
docling-layout, docling-math and other docling-serve adapters.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast


def float_or_none(value: Any) -> float | None:
    """Cast ``value`` to float, returning None on failure or non-finite numbers."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def extract_page_number(prov: Mapping[str, Any], default: int = 1) -> int:
    """Extract a 1-based page number from a Docling provenance record.

    Docling returns ``page_no`` in 1.30+ and occasionally ``page`` or
    ``page_number`` in synthetic payloads or legacy sidecars.
    """
    for key in ("page_no", "page", "page_number"):
        val = prov.get(key)
        if val is not None:
            try:
                num = int(val)
                if num >= 1:
                    return num
            except (ValueError, TypeError):
                pass
    return default


def page_height(json_content: Mapping[str, Any], page: Any) -> float | None:
    """Extract page height for ``page`` from docling json_content.

    Handles pages represented either as a dictionary mapping page numbers to page
    objects, or as a list of page objects.
    """
    pages = json_content.get("pages") or {}
    entry: Mapping[str, Any] | None = None

    if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
        for index, item in enumerate(pages):
            if isinstance(item, Mapping):
                num = item.get("page_number")
                if num == page or str(num) == str(page) or (num is None and index + 1 == page):
                    entry = item
                    break
    elif isinstance(pages, Mapping):
        raw_entry = pages.get(str(page)) or pages.get(page)
        if isinstance(raw_entry, Mapping):
            entry = raw_entry

    if entry is None:
        return None

    height = entry.get("height")
    size = entry.get("size")
    if height is None and isinstance(size, Mapping):
        height = size.get("height")

    return float_or_none(height)


def top_left_bbox(bbox: Any, page_height: float | None) -> tuple[float, float, float, float] | None:
    """Normalize a Docling bbox to [left, top, right, bottom] with a top-left origin.

    Docling dict boxes carry ``coord_origin``; ``BOTTOMLEFT`` boxes are flipped
    against the page height and dropped when that height is unknown. List boxes
    have no origin and are taken as top-left.
    """
    origin = "TOPLEFT"
    if isinstance(bbox, Mapping):
        origin = str(bbox.get("coord_origin") or "TOPLEFT").upper()
        values = [
            bbox.get(short, bbox.get(long))
            for short, long in (("l", "left"), ("t", "top"), ("r", "right"), ("b", "bottom"))
        ]
    elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        values = list(bbox[:4])
    else:
        return None

    coords = [float_or_none(value) for value in values]
    if any(value is None for value in coords):
        return None
    left, top, right, bottom = (cast(float, value) for value in coords)
    if origin == "BOTTOMLEFT":
        if page_height is None:
            return None
        top, bottom = page_height - top, page_height - bottom
    elif origin != "TOPLEFT":
        return None
    return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))


__all__ = ["extract_page_number", "float_or_none", "page_height", "top_left_bbox"]
