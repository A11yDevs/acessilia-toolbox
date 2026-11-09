"""Facade over MinerU MiddleJson (DocVortex 2.0) and legacy payloads.

MinerU 4 returns ``schema: docvortex.middle`` (protocol 2.0): ordered ``pages``
with normalized ``[0, 1]`` bboxes and source-page geometry in
``extensions.docvortex_layout``. Older MinerU releases return ``pdf_info`` with
``preproc_blocks``, point/pixel bboxes and ``page_size``.  This facade exposes
the same attribute-style navigation the normalization builder expects from
``DoclingServeDocument``: iterable collections of texts, tables, pictures and
formulas with bbox and page metadata hoisted onto each item.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from acessilia_toolbox.providers.table_formats import html_table

# Legacy MinerU pipeline block types (inlined to avoid importing the mineru
# package here — the toolbox must stay free of ML runtimes).
TEXT_TYPES = ("text", "title", "chapter", "paragraph", "heading", "list", "index", "code")
TABLE_TYPES = ("table",)
IMAGE_TYPES = ("image", "chart")
FORMULA_TYPES = ("interline_equation", "inline_equation", "equation")

#: Labels the facade exposes for text-bearing items in either protocol.
TEXT_LABELS = frozenset(
    {
        "text",
        "title",
        "heading",
        "paragraph",
        "chapter",
        "list",
        "index",
        "list_item",
        "code",
        "caption",
        "footnote",
        "page_header",
        "page_footer",
    }
)

#: MinerU 4 / DocVortex 2.0 type -> Docling-style label. Types not listed keep
#: their original name. ``list``/``index`` are containers and are never
#: emitted themselves; their children are flattened in reading order.
_V4_LABEL_BY_TYPE = {
    "doc_title": "title",
    "paragraph_title": "heading",
    "text": "text",
    "ref_text": "text",
    "aside_text": "text",
    "header": "page_header",
    "footer": "page_footer",
    "page_number": "page_footer",
    "page_footnote": "footnote",
    "equation": "formula",
    "interline_equation": "formula",
    "inline_equation": "formula",
    "table": "table",
    "image": "image",
    "chart": "picture",
    "code": "code",
    "image_caption": "caption",
    "table_caption": "caption",
    "chart_caption": "caption",
    "code_caption": "caption",
    "image_footnote": "footnote",
    "table_footnote": "footnote",
    "chart_footnote": "footnote",
    "code_footnote": "footnote",
}

#: V4 block types whose ``content`` is a list of inline spans.
_V4_INLINE_TYPES = frozenset(
    {
        "text",
        "ref_text",
        "doc_title",
        "paragraph_title",
        "header",
        "footer",
        "page_number",
        "aside_text",
        "page_footnote",
        "image_caption",
        "image_footnote",
        "table_caption",
        "table_footnote",
        "chart_caption",
        "chart_footnote",
        "code_caption",
        "code_footnote",
        "algorithm_body",
    }
)

#: V4 container types whose children are emitted instead of the parent.
_V4_CONTAINER_TYPES = frozenset({"list", "index"})

#: V4 visual parents: emitted once, with their annotation children afterwards.
_V4_VISUAL_TYPES = frozenset({"image", "table", "chart", "code"})

#: Annotation children of a visual parent that carry visible text.
_V4_ANNOTATION_TYPES = frozenset(
    {
        "image_caption",
        "image_footnote",
        "table_caption",
        "table_footnote",
        "chart_caption",
        "chart_footnote",
        "code_caption",
        "code_footnote",
    }
)

_DISCARDABLE_KEYS = frozenset({"preproc_blocks", "discarded_blocks", "page_idx", "page_size"})


def _inline_text(parts: Any) -> str:
    """Flatten V4 inline spans into their visible text.

    Handles ``text``, ``equation_inline``, ``code_inline`` and ``hyperlink``
    recursively, preserving inline formulas, code and linked text instead of
    dropping everything the old flat reader did not recognise.
    """
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    pieces: list[str] = []
    for part in parts:
        if isinstance(part, str):
            pieces.append(part)
            continue
        if not isinstance(part, Mapping):
            continue
        content = part.get("content")
        if isinstance(content, str):
            pieces.append(content)
        elif isinstance(content, list):
            pieces.append(_inline_text(content))
    return "".join(pieces)


def _dimension(value: Any) -> float | None:
    try:
        dimension = float(value)
    except (TypeError, ValueError):
        return None
    return dimension if dimension > 0 else None


def _page_index(page: Mapping[str, Any], fallback: int = 0) -> int:
    value = page.get("page_idx")
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, (str, float)):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return fallback
    return fallback


class _BboxProxy:
    """Read-only [x0, y0, x1, y1] accessor over a block's bbox list.

    Property names follow the Docling bbox convention (``l/t/r/b``); the
    single-letter accessors carry a lint exemption because they match the
    existing normalization builder contract.
    """

    __slots__ = ("_data", "_origin")

    def __init__(self, data: Sequence[float] | None, origin: str = "TOPLEFT") -> None:
        self._data = list(data) if data else []
        self._origin = origin

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
        return _LabelProxy(self._origin)


class _LabelProxy:
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


class _ProvProxy:
    """Provenance record compatible with the normalization builder."""

    __slots__ = ("bbox", "page_no")

    def __init__(
        self,
        page_no: int,
        bbox: Sequence[float] | None,
        origin: str = "TOPLEFT",
    ) -> None:
        self.page_no = page_no
        self.bbox = _BboxProxy(bbox, origin)


class _ItemProxy:
    """Uniform read access to one MinerU block regardless of nesting depth."""

    __slots__ = ("_block", "_label", "_level", "_normalized", "_page_idx", "_page_size")

    def __init__(
        self,
        block: Mapping[str, Any],
        page_idx: int,
        page_size: Sequence[float] | None,
        *,
        normalized: bool = False,
        label: str | None = None,
        level: int | None = None,
    ) -> None:
        self._block = block
        self._page_idx = page_idx
        self._page_size = list(page_size or [])
        self._normalized = normalized
        self._label = label
        self._level = level

    @property
    def raw_label(self) -> str:
        """Original MinerU block type, before Docling-style normalization."""
        return str(self._block.get("type", "unknown"))

    @property
    def label(self) -> str:
        if self._label is not None:
            return self._label
        raw = self.raw_label
        if self._normalized:
            return _V4_LABEL_BY_TYPE.get(raw, raw)
        return "formula" if raw in FORMULA_TYPES else raw

    @property
    def level(self) -> int | None:
        if self._level is not None:
            return self._level
        raw_level = self._block.get("level")
        if isinstance(raw_level, int) and not isinstance(raw_level, bool):
            return raw_level
        return None

    @property
    def page_no(self) -> int:
        return self._page_idx

    def _bbox_values(self) -> list[float] | None:
        raw = self._block.get("bbox")
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            return None
        try:
            values = [float(value) for value in raw[:4]]
        except (TypeError, ValueError):
            return None
        if not self._normalized:
            return values
        width = _dimension(self._page_size[0] if len(self._page_size) > 0 else None)
        height = _dimension(self._page_size[1] if len(self._page_size) > 1 else None)
        if width is None or height is None:
            # Normalized coordinates without source-page geometry cannot be
            # expressed in points; keep them but do not claim a point origin.
            return values
        return [
            values[0] * width,
            values[1] * height,
            values[2] * width,
            values[3] * height,
        ]

    def _bbox_origin(self) -> str:
        if not self._normalized:
            return "TOPLEFT"
        width = _dimension(self._page_size[0] if len(self._page_size) > 0 else None)
        height = _dimension(self._page_size[1] if len(self._page_size) > 1 else None)
        return "TOPLEFT" if width is not None and height is not None else "UNKNOWN"

    @property
    def bbox(self) -> _BboxProxy:
        return _BboxProxy(self._bbox_values(), self._bbox_origin())

    @property
    def prov(self) -> list[_ProvProxy]:
        """Provenance records in the Docling contract (page_no is 1-based)."""
        return [
            _ProvProxy(
                page_no=self._page_idx + 1,
                bbox=self._bbox_values(),
                origin=self._bbox_origin(),
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
        text = (self.latex or self._block_text()) if self.label == "formula" else self._block_text()
        return str(text) if text is not None else ""

    def _block_text(self) -> Any:
        if self._normalized:
            return self._v4_text()
        if "text" in self._block:
            return self._block["text"]
        content = self._block.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("content") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") in ("text", "code", "paragraph")
            )
        lines = self._block.get("lines") or []
        pieces: list[str] = []
        for line in lines:
            parts: list[str] = []
            for span in line.get("spans", []):
                content = span.get("content") or span.get("text") or span.get("latex")
                if content is not None:
                    parts.append(str(content))
            piece = "".join(parts).strip()
            if piece:
                pieces.append(piece)
        return " ".join(pieces) if pieces else None

    def _v4_text(self) -> str | None:
        raw = self.raw_label
        content = self._block.get("content")
        if raw in _V4_INLINE_TYPES:
            text = _inline_text(content)
            return text or None
        if raw == "equation":
            return content if isinstance(content, str) and content else None
        if raw == "code":
            return self._v4_code_text()
        return None

    def _v4_code_text(self) -> str | None:
        pieces: list[str] = []
        for child in self._block.get("content") or []:
            if not isinstance(child, Mapping):
                continue
            child_type = child.get("type")
            content = child.get("content")
            if child_type == "code_body" and isinstance(content, str):
                pieces.append(content)
            elif child_type == "algorithm_body":
                pieces.append(_inline_text(content))
        text = "\n".join(piece for piece in pieces if piece)
        return text or None

    @property
    def table_ast(self) -> dict[str, Any] | None:
        return html_table(self.html) if self.label == "table" else None

    @property
    def html(self) -> str | None:
        """Table body HTML, when the block carries one."""
        if self._normalized:
            for part in self._block.get("content", []) or []:
                if isinstance(part, Mapping) and part.get("type") == "table_body":
                    value = part.get("content")
                    if isinstance(value, str) and value:
                        return value
            return None
        for part in self._block.get("content", []) or []:
            if isinstance(part, dict) and part.get("type") in ("table", "html"):
                return str(part.get("content") or part.get("html") or "") or None
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
        if self._normalized:
            if self.raw_label in ("equation", "interline_equation", "inline_equation"):
                content = self._block.get("content")
                if isinstance(content, str) and content:
                    return content
            return None
        if self._block.get("latex"):
            return str(self._block["latex"])
        for part in self._block.get("content", []) or []:
            if isinstance(part, dict) and part.get("type") in (
                "equation", "inline_equation", "interline_equation", "latex"
            ):
                return str(part.get("content") or part.get("latex") or "") or None
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
        self._normalized = payload.get("schema") == "docvortex.middle"
        sizes = self._layout_page_sizes(payload)
        self._pages: list[Mapping[str, Any]] = [
            {
                **page,
                "page_size": page.get("page_size")
                or sizes.get(_page_index(page, index), []),
            }
            for index, page in enumerate(payload.get("pages") or payload.get("pdf_info") or [])
        ]

    @staticmethod
    def _layout_page_sizes(payload: Mapping[str, Any]) -> dict[int, list[Any]]:
        sizes: dict[int, list[Any]] = {}
        extensions = payload.get("extensions")
        if not isinstance(extensions, Mapping):
            return sizes
        layout = extensions.get("docvortex_layout")
        if not isinstance(layout, Mapping):
            return sizes
        for entry in layout.get("pages") or []:
            if not isinstance(entry, Mapping):
                continue
            page_idx = entry.get("page_idx")
            if isinstance(page_idx, int) and not isinstance(page_idx, bool):
                sizes[page_idx] = [entry.get("width_pt"), entry.get("height_pt")]
        return sizes

    # -- pages -----------------------------------------------------------

    @property
    def pages(self) -> dict[int, _PageProxy]:
        return {
            _page_index(page, index) + 1: _PageProxy(page)
            for index, page in enumerate(self._pages)
        }

    @property
    def page_count(self) -> int:
        return len(self._pages)

    # -- block iteration ---------------------------------------------------

    def _page_proxies(
        self,
        include_discarded: bool = False,
    ) -> Iterator[tuple[_ItemProxy, int]]:
        """Yield ``(item, tree_level)`` for every exposed block, in reading order."""
        level = 1
        for page in sorted(self._pages, key=lambda entry: _page_index(entry)):
            page_idx = _page_index(page)
            page_size = page.get("page_size") or []
            if self._normalized:
                for block in page.get("blocks") or []:
                    if isinstance(block, Mapping):
                        yield from self._v4_items(block, page_idx, page_size, depth=2)
                continue
            sources: list[Mapping[str, Any]] = list(
                page.get("blocks") or page.get("preproc_blocks") or []
            )
            if include_discarded:
                sources += list(page.get("discarded_blocks") or [])
            for block in sources:
                proxy = _ItemProxy(block, page_idx, page_size)
                if block.get("type") in ("title", "heading", "chapter"):
                    yield proxy, 1
                    level = 2
                else:
                    yield proxy, max(2, level)

    def _v4_items(
        self,
        block: Mapping[str, Any],
        page_idx: int,
        page_size: Sequence[float] | None,
        *,
        depth: int,
        label: str | None = None,
    ) -> Iterator[tuple[_ItemProxy, int]]:
        """Flatten one V4 block tree into reading-order items."""
        raw = str(block.get("type", "unknown"))
        if raw == "discarded":
            return
        if raw in _V4_CONTAINER_TYPES:
            for child in block.get("content") or []:
                if isinstance(child, Mapping):
                    yield from self._v4_items(
                        child,
                        page_idx,
                        page_size,
                        depth=depth + 1,
                        label="list_item" if raw == "list" else None,
                    )
            return
        if raw in _V4_VISUAL_TYPES:
            yield _ItemProxy(
                block,
                page_idx,
                page_size,
                normalized=True,
                label=label,
                level=depth,
            ), depth
            for child in block.get("content") or []:
                if isinstance(child, Mapping) and child.get("type") in _V4_ANNOTATION_TYPES:
                    yield _ItemProxy(
                        child,
                        page_idx,
                        page_size,
                        normalized=True,
                        level=depth,
                    ), depth
            return
        item_level = depth
        if raw == "doc_title":
            item_level = 1
        elif raw == "paragraph_title":
            raw_level = block.get("level")
            item_level = (
                raw_level
                if isinstance(raw_level, int) and not isinstance(raw_level, bool)
                else 2
            )
        yield _ItemProxy(
            block,
            page_idx,
            page_size,
            normalized=True,
            label=label,
            level=item_level,
        ), item_level

    def _blocks_of(
        self,
        types: tuple[str, ...],
        include_discarded: bool = False,
    ) -> Iterator[_ItemProxy]:
        for proxy, _level in self._page_proxies(include_discarded):
            if proxy.label in types:
                yield proxy

    def iterate_items(
        self, with_groups: bool = True, traverse_pictures: bool = True, **_: Any
    ) -> Iterator[tuple[_ItemProxy, int]]:
        """Yield ``(item, tree_level)`` in reading order.

        Mirrors the Docling ``iterate_items`` contract consumed by the
        normalization builder: pairs of an item proxy and its nesting level.
        MinerU's middle_json is page-oriented, so levels are derived from the
        heading structure — titles are level 1, everything else level 2.
        """
        yield from self._page_proxies()

    @property
    def texts(self) -> list[_ItemProxy]:
        return list(self._blocks_of(tuple(TEXT_LABELS)))

    @property
    def tables(self) -> list[_ItemProxy]:
        return list(self._blocks_of(TABLE_TYPES))

    @property
    def pictures(self) -> list[_ItemProxy]:
        return list(self._blocks_of(("image", "picture")))

    @property
    def formulas(self) -> list[_ItemProxy]:
        return list(self._blocks_of((*FORMULA_TYPES, "formula")))

    # -- whole-document text ----------------------------------------------

    @property
    def full_text(self) -> str:
        parts: list[str] = []
        for proxy, _level in self._page_proxies():
            text = proxy.text
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    # -- metadata ------------------------------------------------------------

    @property
    def backend(self) -> str | None:
        backend = self._payload.get("_backend")
        if isinstance(backend, str):
            return backend
        extensions = self._payload.get("extensions")
        mineru = extensions.get("mineru") if isinstance(extensions, Mapping) else None
        tier = mineru.get("tier") if isinstance(mineru, Mapping) else None
        return str(tier) if tier is not None else None

    @property
    def version_name(self) -> str | None:
        version = self._payload.get("_version_name")
        if isinstance(version, str):
            return version
        metadata = self._payload.get("metadata")
        producer = metadata.get("producer") if isinstance(metadata, Mapping) else None
        version = producer.get("version") if isinstance(producer, Mapping) else None
        return str(version) if version is not None else None

    @property
    def raw(self) -> dict[str, Any]:
        return dict(self._payload)

    @property
    def metadata(self) -> dict[str, Any]:
        return {key: value for key, value in self._payload.items() if key not in _DISCARDABLE_KEYS}


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
