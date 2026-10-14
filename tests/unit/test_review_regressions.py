"""Regression cases from PR #37 review."""

from types import SimpleNamespace

import pytest
from tests.unit.test_audit_followup_regressions import make_dataset_adapter

from acessilia_toolbox.core.artifact import NullCache
from acessilia_toolbox.core.errors import ProviderExecutionError
from acessilia_toolbox.core.normalization.table_ast import (
    linearize_table_for_text,
    rows_from_table_ast,
)
from acessilia_toolbox.core.preprocessing.orient import detect_and_orient_image
from acessilia_toolbox.providers.mineru_document import FORMULA_TYPES, MineruDocument
from acessilia_toolbox.providers.mineru_layout import _classify
from acessilia_toolbox.providers.table_formats import docling_table


def test_nullable_orientation_dimensions_still_rotate():
    import base64

    document = {
        "angle": 90,
        "confidence": 0.9,
        "original_width": None,
        "original_height": None,
        "oriented_width": None,
        "oriented_height": None,
        "image_base64": base64.b64encode(b"rotated").decode(),
    }
    adapter = SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(document=document))
    payload, result = detect_and_orient_image(b"original", orient_provider=adapter)
    assert payload == b"rotated"
    assert result.auto_rotated


@pytest.mark.parametrize("cell", ["a", None, 3, []])
def test_invalid_native_grid_is_provider_error(cell):
    with pytest.raises(ProviderExecutionError):
        docling_table({"grid": [[cell]]})


def test_docling_rowspans_keep_column_labels():
    cells = [
        {
            "text": f"H{c}",
            "start_row_offset_idx": 0,
            "start_col_offset_idx": c,
            "end_row_offset_idx": 1,
            "end_col_offset_idx": c + 1,
            "column_header": True,
        }
        for c in range(3)
    ]
    for r, c, text, end in [
        (1, 0, "A", 3),
        (1, 1, "B", 2),
        (1, 2, "C", 2),
        (2, 1, "D", 3),
        (2, 2, "E", 3),
    ]:
        cells.append(
            {
                "text": text,
                "start_row_offset_idx": r,
                "start_col_offset_idx": c,
                "end_row_offset_idx": end,
                "end_col_offset_idx": c + 1,
            }
        )
    table = docling_table({"num_rows": 3, "num_cols": 3, "table_cells": cells})
    assert rows_from_table_ast(table) == [["H0", "H1", "H2"], ["A", "B", "C"], ["A", "D", "E"]]
    assert linearize_table_for_text({"table_ast": table}) == [
        "Row 1: H0: A; H1: B; H2: C",
        "Row 2: H0: A; H1: D; H2: E",
    ]


def test_fully_covered_rows_are_not_empty_rows():
    table = docling_table(
        {
            "num_rows": 2,
            "num_cols": 1,
            "table_cells": [
                {
                    "text": "A",
                    "start_row_offset_idx": 0,
                    "start_col_offset_idx": 0,
                    "end_row_offset_idx": 2,
                    "end_col_offset_idx": 1,
                }
            ],
        }
    )
    assert len(table["body"]) == 1
    assert table["body"][0]["cells"][0]["rowspan"] == 1
    assert linearize_table_for_text({"table_ast": table}) == ["Row 1: A"]


def test_mirror_without_execution_cache_avoids_repeated_remote_fetch(tmp_path):
    adapter = make_dataset_adapter(tmp_path)
    adapter._cache = NullCache()
    calls = []

    def fetch(*args):
        calls.append(args)
        return b"data", "text/plain"

    adapter._provider = SimpleNamespace(get_artifact=fetch)
    for _ in range(2):
        assert adapter._get_artifact_maybe_mirrored("demo", "file.txt", "v1") == (
            b"data",
            "text/plain",
        )
    assert len(calls) == 1
    adapter._get_artifact_maybe_mirrored("demo", "file.txt", "v2")
    assert len(calls) == 2


@pytest.mark.parametrize("kind", sorted(FORMULA_TYPES))
def test_layout_recognizes_every_document_formula_type(kind):
    document = MineruDocument(
        {
            "pdf_info": [
                {
                    "page_idx": 0,
                    "page_size": [100, 100],
                    "preproc_blocks": [{"type": kind, "bbox": [0, 0, 10, 10]}],
                }
            ]
        }
    )
    assert _classify(document)[0]["type"] == "formula"
