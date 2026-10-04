"""Validate TeleOCR geometry and expose the document normalization interface."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field, FiniteFloat, PositiveInt, field_validator


class TeleOCRBlock(BaseModel):
    """Boxes are normalized in the original image, independent of crop rotation."""

    type: str = Field(min_length=1)
    bbox: list[FiniteFloat] | None = None
    angle: int | None = 0
    content: str | None = None

    @field_validator("bbox")
    @classmethod
    def valid_box(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if len(value) not in {4, 8} or any(v < 0 or v > 1 for v in value):
            raise ValueError("bbox must have 4 or 8 finite coordinates in [0, 1]")
        if len(value) == 4 and (value[0] > value[2] or value[1] > value[3]):
            raise ValueError("bbox edges are reversed")
        return value

    @field_validator("angle")
    @classmethod
    def valid_angle(cls, value: int | None) -> int:
        if value not in {None, 0, 90, 180, 270}:
            raise ValueError("angle must be 0, 90, 180 or 270")
        return value or 0


class TeleOCRPage(BaseModel):
    size: tuple[PositiveInt, PositiveInt]
    infer_size: tuple[PositiveInt, PositiveInt] | None = None
    blocks: list[TeleOCRBlock]


LABELS = {
    "title": "heading",
    "text": "paragraph",
    "ref_text": "paragraph",
    "aside_text": "paragraph",
    "phonetic": "paragraph",
    "image": "picture",
    "seal": "picture",
    "table": "table",
    "equation": "formula",
    "equation_block": "formula",
    "header": "page_header",
    "footer": "page_footer",
    "page_number": "page_footer",
    "image_caption": "caption",
    "table_caption": "caption",
    "code_caption": "caption",
    "page_footnote": "footnote",
    "table_footnote": "footnote",
    "image_footnote": "footnote",
    "code": "code",
    "algorithm": "code",
    "list": "list_item",
}


class TeleOCRDocument:
    """Single raster page, retaining unknown labels and absent confidence."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.page = TeleOCRPage.model_validate(payload)
        self._items: list[tuple[Any, int]] = []
        width, height = self.page.size
        for index, block in enumerate(self.page.blocks):
            bbox = None
            if block.bbox is not None:
                box = block.bbox
                if len(box) == 8:
                    box = [min(box[::2]), min(box[1::2]), max(box[::2]), max(box[1::2])]
                bbox = SimpleNamespace(
                    l=box[0] * width,
                    t=box[1] * height,
                    r=box[2] * width,
                    b=box[3] * height,
                    coord_origin=SimpleNamespace(value="TOPLEFT"),
                )
            item = SimpleNamespace(
                self_ref=f"#/teleocr/{index}",
                label=LABELS.get(block.type, "unknown"),
                raw_label=block.type,
                text=block.content,
                level=1,
                confidence=None,
                prov=[SimpleNamespace(page_no=1, bbox=bbox)],
                normalization_metadata={
                    "rotation_degrees": block.angle,
                    "confidence_available": False,
                },
            )
            self._items.append((item, 1 if item.label == "heading" else 0))

    def iterate_items(self, **_: Any) -> Any:
        return iter(self._items)

    def num_pages(self) -> int:
        return 1

    @property
    def pages(self) -> dict[int, Any]:
        width, height = self.page.size
        return {1: SimpleNamespace(size=SimpleNamespace(width=width, height=height))}
