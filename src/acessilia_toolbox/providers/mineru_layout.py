"""MinerU-backed layout analysis provider.

Reuses the single ``/file_parse`` call (via :class:`MineruProvider`) and
classifies the returned blocks into the semantic region categories used by
``document.layout.analyze``, mirroring ``DoclingLayoutProvider``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.providers.mineru import MineruProvider
from acessilia_toolbox.providers.mineru_document import MineruDocument

# Canonical region categories (same vocabulary as DoclingLayoutProvider).
_REGION_TYPES = {
    "text": {"text", "index"},
    "title": {"title"},
    "list_block": {"list"},
    "table": {"table"},
    "embedded_image": {"image"},
    "formula": {"interline_equation"},
    "ignore": {"discarded"},
}


class MineruLayoutProvider(MineruProvider):
    """Extracts layout regions from the MinerU middle_json tree."""

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        result = super().execute(
            capability_id,
            payload,
            filename=filename,
            media_type=media_type,
            parameters=parameters,
        )
        document = result.document
        if not isinstance(document, MineruDocument):
            return result

        regions = _classify(document)
        result.configuration["layout_regions"] = regions
        result.configuration["region_count"] = len(regions)
        result.configuration["backend"] = "mineru-layout"
        return result


def _classify(document: MineruDocument) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for item in document._blocks_of(
        tuple(t for types in _REGION_TYPES.values() for t in types),
        include_discarded=True,
    ):
        label = item.label
        region = "unknown"
        for category, types in _REGION_TYPES.items():
            if label in types:
                region = category
                break
        l_, t_, r_, b_ = item.bbox.as_tuple()
        regions.append(
            {
                "type": region,
                "source_label": label,
                "page": item.page_no,
                "bbox": {"left": l_, "top": t_, "right": r_, "bottom": b_},
                "text_preview": item.text[:120] if item.text else None,
            }
        )
    regions.sort(key=lambda region: (region["page"], region["bbox"]["top"]))
    return regions


__all__ = ["MineruLayoutProvider"]
