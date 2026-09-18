"""Adapts Nougat transcription payloads to the shape expected by the builder.

Nougat outputs mathematical and academic document transcriptions with inline/block LaTeX
and markdown formatting. This facade parses pages and blocks into the attribute interface
the normalization builder iterates over.
"""

from __future__ import annotations

import re
from typing import Any

HEADING_REGEX = re.compile(r"^(#{1,6})\s+(.*)$")


class NougatDocument:
    """Document facade over Nougat markdown or multi-page output."""

    def __init__(self, payload: dict[str, Any] | str) -> None:
        if isinstance(payload, str):
            self._payload: dict[str, Any] = {"text": payload, "pages": [{"text": payload}]}
        else:
            self._payload = payload
        self._items: list[tuple[Any, int]] = []
        self._build_items()

    def _build_items(self) -> None:
        pages_raw = self._payload.get("pages")
        if not pages_raw:
            text = self._payload.get("text", "")
            pages_raw = [{"text": text, "page_number": 1}]

        item_idx = 0
        for page_idx, page in enumerate(pages_raw, start=1):
            page_no = page.get("page_number", page_idx)
            text_content = page.get("text") or page.get("markdown") or ""
            blocks = _parse_nougat_text(text_content)

            for block in blocks:
                item_idx += 1
                item_data = {
                    "self_ref": f"#/elements/{item_idx}",
                    "label": block["label"],
                    "text": block["text"],
                    "level": block.get("level", 1),
                    "prov": [
                        {
                            "page_no": page_no,
                            "bbox": block.get("bbox", {"left": 0, "top": 0, "right": 0, "bottom": 0}),
                        }
                    ],
                }
                if "table" in block:
                    item_data["table"] = block["table"]
                self._items.append((_NougatItemProxy(item_data), block.get("tree_level", 1)))

    def iterate_items(self, **_: Any) -> Any:
        return iter(self._items)

    def num_pages(self) -> int:
        pages = self._payload.get("pages", [])
        return len(pages) if isinstance(pages, list) else 1

    @property
    def pages(self) -> dict[int, Any]:
        pages = self._payload.get("pages", [])
        if not pages:
            return {1: _NougatPageProxy({"size": {"width": 595, "height": 842}})}
        result: dict[int, Any] = {}
        for index, page in enumerate(pages, start=1):
            page_no = page.get("page_number", index)
            size = page.get("size") or {"width": 595, "height": 842}
            result[page_no] = _NougatPageProxy({"size": size})
        return result


def _parse_nougat_text(text: str) -> list[dict[str, Any]]:
    """Split page markdown into semantic blocks (headings, formulas, tables, paragraphs)."""
    blocks: list[dict[str, Any]] = []
    paragraphs = text.split("\n\n")

    for raw_para in paragraphs:
        para = raw_para.strip()
        if not para:
            continue

        # Check for section headings
        heading_match = HEADING_REGEX.match(para)
        if heading_match:
            hashes, title = heading_match.groups()
            level = len(hashes)
            blocks.append({
                "label": "heading",
                "text": title.strip(),
                "level": level,
                "tree_level": level,
            })
            continue

        # Check for LaTeX equations (block \[...\], \begin{equation...}, or $$...$$)
        if (
            (para.startswith(r"\[") and para.endswith(r"\]"))
            or (para.startswith(r"$$") and para.endswith(r"$$") and len(para) > 4)
            or para.startswith(r"\begin{equation")
        ):
            blocks.append({
                "label": "formula",
                "text": para,
                "level": 1,
                "tree_level": 1,
            })
            continue

        # Check for LaTeX tables
        if para.startswith(r"\begin{table") or para.startswith(r"\begin{tabular"):
            blocks.append({
                "label": "table",
                "text": para,
                "table": {"raw": para},
                "level": 1,
                "tree_level": 1,
            })
            continue

        # Regular text paragraph
        # Note: Sub-paragraph inline math ($...$) remains preserved within paragraph text
        # for downstream AST or verbalization passes.
        blocks.append({
            "label": "paragraph",
            "text": para,
            "level": 1,
            "tree_level": 1,
        })

    return blocks


class _NougatItemProxy:
    """Attribute access wrapper over a single item."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name == "label":
            return _NougatLabelProxy(self._data.get("label", "unknown"))
        if name == "prov":
            return [_NougatProvProxy(entry) for entry in (self._data.get("prov") or [])]
        if name == "parent":
            return None
        if name == "self_ref":
            return self._data.get("self_ref")
        if name in ("level", "confidence", "score"):
            return self._data.get(name)
        if name in ("text", "orig", "name", "marker", "enumerated", "content_layer"):
            return self._data.get(name)
        if name == "table":
            return self._data.get("table")
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return self._data


class _NougatLabelProxy:
    def __init__(self, value: str) -> None:
        self.value = value


class _NougatProvProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.page_no = data.get("page_no", 1)
        bbox = data.get("bbox")
        self.bbox = _NougatBboxProxy(bbox) if bbox else None
        self.charspan = None


class _NougatBboxProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.l = float(data.get("left", data.get("l", 0)))
        self.t = float(data.get("top", data.get("t", 0)))
        self.r = float(data.get("right", data.get("r", 0)))
        self.b = float(data.get("bottom", data.get("b", 0)))
        self.coord_origin = _NougatLabelProxy("TOPLEFT")


class _NougatPageProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.size = _NougatSizeProxy(data.get("size", {}))


class _NougatSizeProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.width = data.get("width", 595)
        self.height = data.get("height", 842)
