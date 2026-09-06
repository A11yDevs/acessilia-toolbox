"""Adapts the docling-serve document payload to the shape the builder expects.

docling-serve 1.30+ returns separate collections (texts, pictures, tables...)
with a body tree referencing items by `$ref`. These proxies expose that payload
through the attribute interface the normalization builder iterates over, so the
builder never learns about any provider's wire format.
"""

from __future__ import annotations

from typing import Any

# Collections holding renderable items, mapped to their default nesting level.
ITEM_COLLECTIONS = {
    "texts": 1,
    "pictures": 1,
    "tables": 1,
    "groups": 1,
    "key_value_items": 1,
    "form_items": 1,
}


class DoclingServeDocument:
    """Document facade over a docling-serve JSON payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self._items: list[tuple[Any, int]] = []
        self._build_items()

    def _build_items(self) -> None:
        seen: set[str] = set()
        for collection, default_level in ITEM_COLLECTIONS.items():
            for item in self._payload.get(collection, []):
                ref = item.get("self_ref", "")
                if ref and ref in seen:
                    continue
                if ref:
                    seen.add(ref)
                level = item.get("level", default_level)
                if isinstance(level, dict):
                    level = 1
                self._items.append((_ItemProxy(item), level))

    def iterate_items(self, **_: Any) -> Any:
        return iter(self._items)

    def num_pages(self) -> int:
        pages = self._payload.get("pages", {})
        if isinstance(pages, dict | list):
            return len(pages)
        return 0

    @property
    def pages(self) -> dict[int, Any]:
        pages = self._payload.get("pages", {})
        if isinstance(pages, dict):
            return {int(key): _PageProxy(value) for key, value in pages.items()}
        if isinstance(pages, list):
            return {
                page.get("page_number", index): _PageProxy(page)
                for index, page in enumerate(pages)
            }
        return {}


class _ItemProxy:
    """Attribute access over a single docling item."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name == "label":
            return _LabelProxy(self._data.get("label", "unknown"))
        if name == "prov":
            return [_ProvProxy(entry) for entry in (self._data.get("prov") or [])]
        if name == "parent":
            parent = self._data.get("parent")
            return _CrefProxy(parent) if parent else None
        if name == "self_ref":
            return self._data.get("self_ref")
        if name in ("level", "confidence", "score"):
            value = self._data.get(name)
            return None if isinstance(value, dict) else value
        if name in ("text", "orig", "name", "marker", "enumerated", "content_layer"):
            return self._data.get(name)
        if name == "table":
            return self._data.get("table") or self._data.get("data")
        for candidate in ("rows", "data", "cells"):
            value = self._data.get(candidate)
            if value is not None:
                return value
        return None

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return self._data


class _LabelProxy:
    def __init__(self, value: str) -> None:
        self.value = value


class _ProvProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        page = data.get("page_no")
        self.page_no = page if page is not None else data.get("page_number", 1)
        bbox = data.get("bbox")
        self.bbox = _BboxProxy(bbox) if bbox else None
        charspan = data.get("charspan")
        if isinstance(charspan, list | tuple) and len(charspan) == 2:
            self.charspan: tuple[int, int] | None = (int(charspan[0]), int(charspan[1]))
        else:
            self.charspan = None


class _BboxProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        # Coordinates are read with an explicit None check: a legitimate 0 edge
        # would be discarded by `or`.
        self.l = _first_present(data, "left", "l")
        self.t = _first_present(data, "top", "t")
        self.r = _first_present(data, "right", "r")
        self.b = _first_present(data, "bottom", "b")
        self.coord_origin = _LabelProxy(data.get("coord_origin", "TOPLEFT"))


class _CrefProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.cref = data.get("$ref") or data.get("cref", "")


class _PageProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.size = _SizeProxy(data.get("size") or data)


class _SizeProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.width = data.get("width")
        self.height = data.get("height")


def _first_present(data: dict[str, Any], *names: str, default: float = 0) -> Any:
    for name in names:
        value = data.get(name)
        if value is not None:
            return value
    return default
