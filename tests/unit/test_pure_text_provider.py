"""pure-text provider behavior for text postprocessing."""

from __future__ import annotations

import json

import pytest

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pure_text import (
    PureTextProvider,
    _apply_markers,
    _cleanup_whitespace,
    _overlaps_clean,
    _postprocess_text,
)


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "pure-text",
        "version": "1.0",
        "transport": "in_process",
        "capabilities": ["text.postprocess"],
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def extract(adapter: PureTextProvider, document: dict):
    return adapter.execute(
        "text.postprocess",
        json.dumps(document).encode("utf-8"),
        filename="document.json",
        media_type="application/json",
    )


SAMPLE_DOCUMENT = {
    "pages": {
        "1": {
            "page_number": 1,
            "elements": [
                {"type": "paragraph", "text": "Primeiro parágrafo."},
                {"type": "code", "text": "def hello():\n    pass"},
                {"type": "paragraph", "text": "Segundo parágrafo."},
                {"type": "table", "text": "Header1|Header2\nCell1|Cell2"},
                {"type": "picture", "text": "[imagem: gráfico]"},
            ],
        },
        "2": {
            "page_number": 2,
            "elements": [
                {"type": "paragraph", "text": "Terceiro parágrafo."},
            ],
        },
    },
}


class TestPureTextProvider:
    def test_postprocess_returns_pages(self) -> None:
        adapter = PureTextProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert result.backend == "pure-python"
        assert result.document["page_count"] == 2

    def test_postprocess_applies_markers(self) -> None:
        adapter = PureTextProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert "code" in result.document["markers_applied"]
        assert "table" in result.document["markers_applied"]

    def test_postprocess_has_full_text(self) -> None:
        adapter = PureTextProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert "Primeiro parágrafo" in result.document["full_text"]
        assert "Terceiro parágrafo" in result.document["full_text"]

    def test_postprocess_deduplicates(self) -> None:
        doc = {
            "pages": {
                "1": {
                    "page_number": 1,
                    "elements": [
                        {"type": "paragraph", "text": "Texto repetido."},
                        {"type": "paragraph", "text": "Texto repetido."},
                    ],
                },
            },
        }
        adapter = PureTextProvider(descriptor())
        result = extract(adapter, doc)

        assert result.document["deduplicated_count"] == 1

    def test_health_returns_healthy(self) -> None:
        adapter = PureTextProvider(descriptor())
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "pure-text"

    def test_versions_returns_version(self) -> None:
        adapter = PureTextProvider(descriptor())
        versions = adapter.versions()
        assert "provider" in versions

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, PureTextProvider)


class TestApplyMarkers:
    def test_code_marker(self) -> None:
        result = _apply_markers("code block", "code")
        assert "[INÍCIO DO CÓDIGO]" in result
        assert "[FIM DO CÓDIGO]" in result

    def test_list_marker(self) -> None:
        result = _apply_markers("list item", "list")
        assert "[INÍCIO DA LISTA]" in result

    def test_unknown_type_returns_unchanged(self) -> None:
        result = _apply_markers("plain text", "unknown")
        assert result == "plain text"


class TestOverlapsClean:
    def test_removes_overlapping_items(self) -> None:
        items = [
            {"text": "high", "confidence": 0.9, "bbox": [0, 0, 100, 100]},
            {"text": "low", "confidence": 0.5, "bbox": [10, 10, 90, 90]},
        ]
        result = _overlaps_clean(items, overlap_threshold=0.3)
        assert len(result) == 1
        assert result[0]["text"] == "high"

    def test_keeps_non_overlapping_items(self) -> None:
        items = [
            {"text": "left", "confidence": 0.9, "bbox": [0, 0, 50, 100]},
            {"text": "right", "confidence": 0.8, "bbox": [60, 0, 100, 100]},
        ]
        result = _overlaps_clean(items)
        assert len(result) == 2

    def test_items_without_bbox_are_kept(self) -> None:
        items = [
            {"text": "no bbox", "confidence": 0.9},
            {"text": "with bbox", "confidence": 0.8, "bbox": [0, 0, 100, 100]},
        ]
        result = _overlaps_clean(items)
        assert len(result) == 2


class TestCleanupWhitespace:
    def test_removes_excessive_blank_lines(self) -> None:
        result = _cleanup_whitespace("a\n\n\n\nb")
        assert result == "a\n\nb"

    def test_removes_trailing_spaces(self) -> None:
        result = _cleanup_whitespace("a   \nb")
        assert "a\nb" in result

    def test_removes_leading_spaces(self) -> None:
        result = _cleanup_whitespace("  a\n  b")
        assert result == "a\nb"

    def test_collapses_multiple_spaces(self) -> None:
        result = _cleanup_whitespace("a    b")
        assert result == "a b"