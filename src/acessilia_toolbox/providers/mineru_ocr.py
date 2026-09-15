"""MinerU-backed OCR provider.

Reuses the single ``/file_parse`` call (via :class:`MineruProvider`) and
flattens recognized text spans into the ``document.ocr`` shape, mirroring
``DoclingOcrProvider``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.providers.mineru import MineruProvider
from acessilia_toolbox.providers.mineru_document import MineruDocument


class MineruOcrProvider(MineruProvider):
    """Extracts OCR text items from the MinerU middle_json tree."""

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

        items: list[dict[str, Any]] = []
        for item in document.texts:
            if not item.text:
                continue
            left, top, right, bottom = item.bbox.as_tuple()
            items.append(
                {
                    "text": item.text,
                    "label": item.label,
                    "page": item.page_no,
                    "bbox": {
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                    },
                }
            )
        items.sort(key=lambda entry: (entry["page"], entry["bbox"]["top"]))

        result.configuration["ocr_items"] = items
        result.configuration["item_count"] = len(items)
        result.configuration["full_text"] = document.full_text
        result.configuration["language"] = str(
            (dict(parameters or {})).get("lang", "pt")
        )
        result.configuration["backend"] = "mineru-ocr"
        return result


__all__ = ["MineruOcrProvider"]
