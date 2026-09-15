"""Facade over the MinerU pipeline middle_json payload.

MinerU returns ``middle_json`` — a page-oriented tree where each page holds
``preproc_blocks`` (and ``discarded_blocks``) typed by :class:`BlockType`, plus
``page_size`` and ``page_idx``.  This facade exposes the same attribute-style
navigation the normalization builder expects from ``DoclingServeDocument``:
iterable collections of texts, tables, pictures and formulas with bbox and
page metadata hoisted onto each item.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

# Block types from mineru.utils.enum_class (inlined to avoid importing the
# mineru package here — the toolbox must stay free of ML runtimes).
TEXT_TYPES = ("text", "title", "list", "index")
TABLE_TYPES = ("table",)
IMAGE_TYPES = ("image",)
FORMULA_TYPES = ("interline_equation",)

_DISCARDABLE_KEYS = frozenset(
    {"preproc_blocks", "discarded_blocks", "page_idx", "page_size"}
)


class _BboxProxy:
    """Read-only [x0, y0, x1, y1] accessor over a block's bbox list.

    Property names follow the Docling bbox convention (``l/t/r/b``); the
    single-letter accessors carry a lint exemption because they match the
    existing normalization builder contract.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Sequence[float] | None) -> None:
        self._data = list(data) if data else []

    @property
    def l(self) -> float:  # noqa: E743 - matches Docling bbox naming
        return float(self._data[0]) if len(self._data) > 0 else 0.0

    @property
    def t(self) -> float:
        return float(self._data[1]) if len(self._data) > 1 else 0.0

    @property
    def r(self) -> float:
        return float(self._data[2]) if len(self._data) > 2 else 0.0

    @property
    def b(self) -> float:
        return float(self._data[3]) if len(self._data) > 3 else 0.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.l, self.t, self.r, self.b)

    @property
    def coord_origin(self) -> _LabelProxy:
        # MinerU bboxes follow the top-left origin convention.
        return _LabelProxy("TOPLEFT")


class _LabelProxy:
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


class _ProvProxy:
    """Provenance record compatible with the normalization builder."""

    __slots__ = ("bbox", "page_no")

    def __init__(self, page_no: int, bbox: Sequence[float] | None) -> None:
        self.page_no = page_no
        self.bbox = _BboxProxy(bbox)


class _ItemProxy:
    """Uniform read access to one MinerU block regardless of nesting depth."""

    __slots__ = ("_block", "_page_idx", "_page_size")

    def __init__(
        self,
        block: Mapping[str, Any],
        page_idx: int,
        page_size: Sequence[float] | None,
    ) -> None:
        self._block = block
        self._page_idx = page_idx
        self._page_size = list(page_size or [])

    @property
    def label(self) -> str:
        return str(self._block.get("type", "unknown"))

    @property
    def page_no(self) -> int:
        return self._page_idx

    @property
    def bbox(self) -> _BboxProxy:
        return _BboxProxy(self._block.get("bbox"))

    @property
    def prov(self) -> list[_ProvProxy]:
        """Provenance records in the Docling contract (page_no is 1-based)."""
        return [
            _ProvProxy(
                page_no=self._page_idx + 1,
                bbox=self._block.get("bbox"),
            )
        ]

    @property
    def self_ref(self) -> str | None:
        return self._block.get("self_ref")

    @property
    def parent(self) -> None:
        return None

    @property
    def text(self) -> str:
        text = self._block_text()
        return str(text) if text is not None else ""

    def _block_text(self) -> Any:
        if "text" in self._block:
            return self._block["text"]
        lines = self._block.get("lines") or []
        parts: list[str] = []
        for line in lines:
            for span in line.get("spans", []):
                content = span.get("content") or span.get("text")
                if content:
                    parts.append(str(content))
        return "".join(parts) if parts else None

    @property
    def html(self) -> str | None:
        """Table body HTML, when the block carries one."""
        for sub in self._block.get("blocks", []) or []:
            if sub.get("type") in ("table_body",):
                for line in sub.get("lines", []) or []:
                    for span in line.get("spans", []) or []:
                        if span.get("type") == "table" and span.get("html"):
                            return str(span["html"])
        # Direct span fallback (flattened representations).
        for line in self._block.get("lines", []) or []:
            for span in line.get("spans", []) or []:
                if span.get("type") == "table" and span.get("html"):
                    return str(span["html"])
        return None

    @property
    def latex(self) -> str | None:
        """Formula LaTeX, when the block carries one."""
        if self._block.get("latex"):
            return str(self._block["latex"])
        for line in self._block.get("lines", []) or []:
            for span in line.get("spans", []) or []:
                content = span.get("content") or span.get("latex")
                equation_types = (
                    "equation",
                    "interline_equation",
                    "inline_equation",
                )
                if span.get("type") in equation_types and content:
                    return str(content)
        return None

    @property
    def raw(self) -> dict[str, Any]:
        return dict(self._block)


class MineruDocument:
    """Facade over a MinerU ``middle_json`` payload."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._payload = payload
        self._pages: list[Mapping[str, Any]] = list(
            payload.get("pdf_info") or []
        )

    # -- pages -----------------------------------------------------------

    @property
    def pages(self) -> dict[int, _PageProxy]:
        return {
            index + 1: _PageProxy(page)
            for index, page in enumerate(self._pages)
        }

    @property
    def page_count(self) -> int:
        return len(self._pages)

    # -- block iteration ---------------------------------------------------

    def _blocks_of(
        self,
        types: tuple[str, ...],
        include_discarded: bool = False,
    ) -> Iterator[_ItemProxy]:
        for page in self._pages:
            page_idx = int(page.get("page_idx", 0))
            page_size = page.get("page_size") or []
            sources: list[Mapping[str, Any]] = list(
                page.get("preproc_blocks") or []
            )
            if include_discarded:
                sources += list(page.get("discarded_blocks") or [])
            for block in sources:
                if block.get("type") in types:
                    yield _ItemProxy(block, page_idx, page_size)

    def iterate_items(
        self, with_groups: bool = True, traverse_pictures: bool = True, **_: Any
    ) -> Iterator[tuple[_ItemProxy, int]]:
        """Yield ``(item, tree_level)`` in reading order.

        Mirrors the Docling ``iterate_items`` contract consumed by the
        normalization builder: pairs of an item proxy and its nesting level.
        MinerU's middle_json is page-oriented, so levels are derived from the
        heading structure — titles are level 1, everything else level 2.
        """
        level = 1
        for page in sorted(
            self._pages, key=lambda p: int(p.get("page_idx", 0))
        ):
            page_idx = int(page.get("page_idx", 0))
            page_size = page.get("page_size") or []
            for block in page.get("preproc_blocks") or []:
                if block.get("type") == "discarded":
                    continue
                proxy = _ItemProxy(block, page_idx, page_size)
                if block.get("type") == "title":
                    yield proxy, 1
                    level = 2
                else:
                    yield proxy, max(2, level)

    @property
    def texts(self) -> list[_ItemProxy]:
        return list(self._blocks_of(TEXT_TYPES))

    @property
    def tables(self) -> list[_ItemProxy]:
        return list(self._blocks_of(TABLE_TYPES))

    @property
    def pictures(self) -> list[_ItemProxy]:
        return list(self._blocks_of(IMAGE_TYPES))

    @property
    def formulas(self) -> list[_ItemProxy]:
        return list(self._blocks_of(FORMULA_TYPES))

    # -- whole-document text ----------------------------------------------

    @property
    def full_text(self) -> str:
        parts: list[str] = []
        for page in self._pages:
            for block in page.get("preproc_blocks") or []:
                if block.get("type") == "discarded":
                    continue
                proxy = _ItemProxy(
                    block,
                    int(page.get("page_idx", 0)),
                    page.get("page_size"),
                )
                text = proxy.text
                if text:
                    parts.append(text)
        return "\n\n".join(parts)

    # -- metadata ------------------------------------------------------------

    @property
    def backend(self) -> str | None:
        return self._payload.get("_backend")

    @property
    def version_name(self) -> str | None:
        return self._payload.get("_version_name")

    @property
    def raw(self) -> dict[str, Any]:
        return dict(self._payload)

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self._payload.items()
            if key not in _DISCARDABLE_KEYS
        }


__all__ = ["MineruDocument"]


class _PageProxy:
    """Page descriptor compatible with the normalization builder."""

    __slots__ = ("size",)

    def __init__(self, page: Mapping[str, Any]) -> None:
        page_size = page.get("page_size") or [None, None]
        self.size = _SizeProxy(page_size)


class _SizeProxy:
    __slots__ = ("height", "width")

    def __init__(self, page_size: Sequence[float | None]) -> None:
        self.width = page_size[0] if len(page_size) > 0 else None
        self.height = page_size[1] if len(page_size) > 1 else None
