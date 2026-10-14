"""Translate provider-native tables at the adapter boundary."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from acessilia_toolbox.core.errors import ProviderExecutionError


def docling_table(data: Any) -> Any:
    """Validate provider-native table fields before exposing the canonical AST."""
    try:
        return _docling_table(data)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise ProviderExecutionError("invalid native table dimensions or coordinates") from exc


def _docling_table(data: Any) -> Any:
    """Preserve native grid positions, header flags and merged cells."""
    if not isinstance(data, dict):
        return data
    if "table_cells" not in data and isinstance(data.get("grid"), list):
        grid = data["grid"]
        cells = []
        for row_index, row in enumerate(grid):
            if not isinstance(row, list):
                raise ProviderExecutionError("native table grid rows must be lists")
            for col_index, cell in enumerate(row):
                if not isinstance(cell, dict):
                    raise ProviderExecutionError("native table grid cells must be objects")
                cells.append(
                    {"start_row_offset_idx": row_index, "start_col_offset_idx": col_index, **cell}
                )
        data = {
            **data,
            "table_cells": cells,
            "num_rows": len(grid),
            "num_cols": max(map(len, grid), default=0),
        }
    if "table_cells" not in data:
        return data
    raw_cells = data.get("table_cells") or []
    if not raw_cells:
        return None
    rows = int(
        data.get("num_rows") or max(int(cell.get("end_row_offset_idx", 1)) for cell in raw_cells)
    )
    cols = int(
        data.get("num_cols") or max(int(cell.get("end_col_offset_idx", 1)) for cell in raw_cells)
    )
    if not (0 < rows <= 10000 and 0 < cols <= 1000 and rows * cols <= 100000):
        raise ProviderExecutionError("native table dimensions exceed supported limits")
    anchors: dict[tuple[int, int], dict[str, Any]] = {}
    covered: set[tuple[int, int]] = set()
    for raw in data.get("table_cells") or []:
        r = int(raw.get("start_row_offset_idx", 0))
        c = int(raw.get("start_col_offset_idx", 0))
        end_r = min(rows, int(raw.get("end_row_offset_idx", r + int(raw.get("row_span", 1)))))
        end_c = min(cols, int(raw.get("end_col_offset_idx", c + int(raw.get("col_span", 1)))))
        if not (0 <= r < end_r and 0 <= c < end_c):
            continue
        column_header = bool(raw.get("column_header"))
        row_header = bool(raw.get("row_header"))
        anchors[r, c] = {
            "text": str(raw.get("text") or ""),
            "header": column_header or row_header,
            "scope": "col" if column_header else "row" if row_header else "none",
            "rowspan": end_r - r,
            "colspan": end_c - c,
            "metadata": {"column_index": c},
        }
        covered.update((rr, cc) for rr in range(r, end_r) for cc in range(c, end_c))
    omitted = {
        r
        for r in range(rows)
        if all((r, c) in covered and (r, c) not in anchors for c in range(cols))
    }
    for (r, _), cell in anchors.items():
        cell["rowspan"] -= sum(rr in omitted for rr in range(r + 1, r + cell["rowspan"]))
    header: list[dict[str, Any]] = []
    body: list[dict[str, Any]] = []
    for r in range(rows):
        if r in omitted:
            continue
        cells = [
            anchors.get((r, c), {"text": ""})
            for c in range(cols)
            if (r, c) in anchors or (r, c) not in covered
        ]
        is_header = bool(cells) and all(cell.get("scope") == "col" for cell in cells)
        target = header if is_header and not body else body
        target.append({"cells": cells})
    # The canonical contract requires a body; a header-only source table is
    # retained in the body with explicit per-cell header flags.
    if not body:
        body, header = header, []
    return {"header": header, "body": body}


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self.depth = 0
        self.section = "body"
        self.row: dict[str, Any] | None = None
        self.cell: dict[str, Any] | None = None
        self.text: list[str] = []

    def _close_cell(self) -> None:
        if self.cell is not None and self.row is not None:
            self.cell["text"] = "".join(self.text).strip()
            self.row["cells"].append(self.cell)
        self.cell = None
        self.text = []

    def _close_row(self) -> None:
        self._close_cell()
        if self.row is not None and self.row["cells"]:
            self.rows.append(self.row)
        self.row = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.depth += 1
        if self.depth != 1:
            return
        values = dict(attrs)
        if tag in {"thead", "tbody", "tfoot"}:
            self._close_row()
            self.section = {"thead": "header", "tbody": "body", "tfoot": "footer"}[tag]
        elif tag == "tr":
            self._close_row()
            self.row = {"cells": [], "section": self.section}
        elif tag in {"td", "th"} and self.row is not None:
            self._close_cell()
            self.cell = {"text": "", "header": tag == "th"}
            if values.get("scope") in {"row", "col", "rowgroup", "colgroup"}:
                self.cell["scope"] = values["scope"]
            elif tag == "th":
                self.cell["scope"] = "col"
            for key in ("colspan", "rowspan"):
                try:
                    self.cell[key] = max(1, min(10000, int(values.get(key) or 1)))
                except ValueError:
                    self.cell[key] = 1
            self.text = []
        elif tag == "br" and self.cell is not None:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self.cell is not None and self.depth == 1:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.depth == 1:
            if tag in {"td", "th"}:
                self._close_cell()
            elif tag in {"tr", "table", "thead", "tbody", "tfoot"}:
                self._close_row()
                if tag in {"thead", "tbody", "tfoot"}:
                    self.section = "body"
        if tag == "table":
            self.depth = max(0, self.depth - 1)


def html_table(html: str | None) -> dict[str, Any] | None:
    if not html:
        return None
    parser = _TableParser()
    parser.feed(html)
    parser.close()
    parser._close_row()
    result: dict[str, Any] = {"body": []}
    for row in parser.rows:
        section = row.pop("section")
        if (
            section == "body"
            and not result["body"]
            and all(cell.get("header") and cell.get("scope") != "row" for cell in row["cells"])
        ):
            section = "header"
        result.setdefault(section, []).append(row)
    if not result["body"]:
        result["body"] = result.pop("header", [])
    return result if result["body"] else None
