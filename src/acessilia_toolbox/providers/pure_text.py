"""Pure-Python text postprocessing provider.

Applies region markers, deduplicates overlapping content, consolidates
page fragments, and cleans up whitespace artifacts from extracted text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Region markers
# ---------------------------------------------------------------------------

_MARKERS = {
    "code": ("[INÍCIO DO CÓDIGO]", "[FIM DO CÓDIGO]"),
    "list": ("[INÍCIO DA LISTA]", "[FIM DA LISTA]"),
    "table": ("[INÍCIO DA TABELA]", "[FIM DA TABELA]"),
    "callout": ("[INÍCIO DO DESTAQUE]", "[FIM DO DESTAQUE]"),
    "image": ("[INÍCIO DA IMAGEM]", "[FIM DA IMAGEM]"),
    "formula": ("[INÍCIO DA FÓRMULA]", "[FIM DA FÓRMULA]"),
}


def _apply_markers(text: str, region_type: str) -> str:
    """Wrap text with region markers for accessibility."""
    markers = _MARKERS.get(region_type)
    if not markers:
        return text
    return f"{markers[0]}\n{text}\n{markers[1]}"


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------


def _overlaps_clean(
    items: list[dict[str, Any]],
    overlap_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Remove items that overlap significantly with higher-confidence items."""
    sorted_items = sorted(
        items,
        key=lambda x: x.get("confidence", 1.0),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    for item in sorted_items:
        bbox = item.get("bbox", [])
        if not bbox or len(bbox) != 4:
            kept.append(item)
            continue
        ax0, ay0, ax1, ay1 = bbox
        a_area = (ax1 - ax0) * (ay1 - ay0)
        if a_area <= 0:
            kept.append(item)
            continue

        overlaps = False
        for kept_item in kept:
            k_bbox = kept_item.get("bbox", [])
            if not k_bbox or len(k_bbox) != 4:
                continue
            kx0, ky0, kx1, ky1 = k_bbox

            ix0 = max(ax0, kx0)
            iy0 = max(ay0, ky0)
            ix1 = min(ax1, kx1)
            iy1 = min(ay1, ky1)

            if ix0 < ix1 and iy0 < iy1:
                inter_area = (ix1 - ix0) * (iy1 - iy0)
                ratio = inter_area / a_area
                if ratio > overlap_threshold:
                    overlaps = True
                    break

        if not overlaps:
            kept.append(item)

    return kept


# ---------------------------------------------------------------------------
# Content fingerprint (deduplication)
# ---------------------------------------------------------------------------


def _content_fingerprint(text: str) -> str:
    """Generate a hash for deduplication of text content."""
    import hashlib
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Whitespace cleanup
# ---------------------------------------------------------------------------


def _cleanup_whitespace(text: str) -> str:
    """Clean up whitespace artifacts from extracted text."""
    # Remove multiple consecutive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove spaces at line beginnings
    text = re.sub(r"^ +", "", text, flags=re.MULTILINE)
    # Remove trailing spaces
    text = re.sub(r" +$", "", text, flags=re.MULTILINE)
    # Collapse multiple spaces within lines
    text = re.sub(r" {2,}", " ", text)
    # Remove spaces before punctuation
    text = re.sub(r"\s+([,;:.!?])", r"\1", text)
    # Normalize paragraph breaks
    text = re.sub(r"\n\n\n+", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------


def _postprocess_text(document: dict[str, Any]) -> dict[str, Any]:
    """Post-process extracted text from a structured document."""
    result: dict[str, Any] = {
        "pages": [],
        "full_text": "",
        "markers_applied": [],
        "deduplicated_count": 0,
        "overlap_removed_count": 0,
    }

    fingerprints: set[str] = set()
    all_text_parts: list[str] = []

    pages = document.get("pages", {})
    if isinstance(pages, dict):
        pages = list(pages.values())

    for page in pages:
        page_num = page.get("page_number", 0) or page.get("page_no", 0)
        elements = page.get("elements", []) or page.get("items", []) or page.get("regions", [])

        page_text_parts: list[str] = []
        page_markers: list[str] = []

        for elem in elements:
            elem_type = elem.get("type", "") or elem.get("label", "")
            text = elem.get("text", "") or elem.get("content", "")

            if not text:
                continue

            # Deduplicate
            fp = _content_fingerprint(text)
            if fp in fingerprints:
                result["deduplicated_count"] += 1
                continue
            fingerprints.add(fp)

            # Apply markers for structured regions
            if elem_type in _MARKERS:
                text = _apply_markers(text, elem_type)
                page_markers.append(elem_type)

            page_text_parts.append(text)
            all_text_parts.append(text)

        page_text = "\n".join(page_text_parts)
        page_text = _cleanup_whitespace(page_text)

        result["pages"].append({
            "page_number": page_num,
            "text": page_text,
            "element_count": len(elements),
        })
        result["markers_applied"].extend(page_markers)

    result["full_text"] = _cleanup_whitespace("\n\n".join(all_text_parts))
    result["page_count"] = len(result["pages"])
    result["markers_applied"] = list(set(result["markers_applied"]))

    return result


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PureTextProvider:
    """Pure-Python text postprocessing provider."""

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
        document = json.loads(payload.decode("utf-8", errors="replace"))
        result = _postprocess_text(document)

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


__all__ = ["PureTextProvider", "_apply_markers", "_overlaps_clean", "_postprocess_text"]
