"""pure-accessibility provider behavior for accessibility marking."""

from __future__ import annotations

import json

import pytest

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pure_accessibility import (
    PureAccessibilityProvider,
    _analyze_accessibility,
)


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "pure-accessibility",
        "version": "1.0",
        "transport": "in_process",
        "capabilities": ["accessibility.mark"],
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def extract(adapter: PureAccessibilityProvider, document: dict):
    return adapter.execute(
        "accessibility.mark",
        json.dumps(document).encode("utf-8"),
        filename="document.json",
        media_type="application/json",
    )


SAMPLE_DOCUMENT = {
    "pages": {
        "1": {
            "page_number": 1,
            "elements": [
                {"type": "picture", "text": "[imagem]", "id": "pic1"},
                {"type": "table", "text": "table data", "id": "tab1"},
                {"type": "formula", "text": "E=mc^2", "id": "frm1"},
                {"type": "code", "text": "def foo(): pass", "id": "cod1"},
                {"type": "paragraph", "text": "Normal text", "id": "par1"},
            ],
        },
    },
}


class TestPureAccessibilityProvider:
    def test_analyze_returns_obligations(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert result.backend == "pure-python"
        assert result.document["obligation_count"] > 0

    def test_analyze_detects_images_without_alt(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert result.document["summary"]["images_without_alt"] == 1

    def test_analyze_detects_tables_without_linearization(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert result.document["summary"]["tables_without_linearization"] == 1

    def test_analyze_detects_formulas_without_verbalization(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert result.document["summary"]["formulas_without_verbalization"] == 1

    def test_analyze_detects_code_without_language(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert result.document["summary"]["code_without_language"] == 1

    def test_analyze_calculates_score(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        assert 0 <= result.document["score"] <= 100

    def test_analyze_obligations_have_rationale(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, SAMPLE_DOCUMENT)

        for obl in result.document["obligations"]:
            assert obl["rationale"]
            assert obl["methods"]
            assert obl["obligation_id"]

    def test_clean_document_scores_100(self) -> None:
        clean = {
            "pages": {
                "1": {
                    "page_number": 1,
                    "elements": [
                        {
                            "type": "picture",
                            "text": "[imagem]",
                            "id": "pic1",
                            "alt_text": "Descrição da imagem",
                        },
                        {
                            "type": "table",
                            "text": "table data",
                            "id": "tab1",
                            "linearized": True,
                        },
                        {
                            "type": "formula",
                            "text": "E=mc^2",
                            "id": "frm1",
                            "mathml": "<math>...</math>",
                        },
                        {
                            "type": "code",
                            "text": "def foo(): pass",
                            "id": "cod1",
                            "language": "python",
                        },
                    ],
                },
            },
        }
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, clean)

        assert result.document["score"] == 100
        assert result.document["obligation_count"] == 0

    def test_health_returns_healthy(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "pure-accessibility"

    def test_versions_returns_version(self) -> None:
        adapter = PureAccessibilityProvider(descriptor())
        versions = adapter.versions()
        assert "provider" in versions

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, PureAccessibilityProvider)

    def test_empty_document_scores_100(self) -> None:
        empty = {"pages": {}}
        adapter = PureAccessibilityProvider(descriptor())
        result = extract(adapter, empty)

        assert result.document["score"] == 100
        assert result.document["obligation_count"] == 0


class TestAnalyzeAccessibility:
    def test_unknown_elements_are_counted(self) -> None:
        doc = {
            "pages": {
                "1": {
                    "page_number": 1,
                    "elements": [
                        {"type": "unknown", "text": "???", "id": "unk1"},
                    ],
                },
            },
        }
        result = _analyze_accessibility(doc)
        assert result["summary"]["unknown_elements"] == 1

    def test_obligations_include_page_number(self) -> None:
        doc = {
            "pages": {
                "1": {
                    "page_number": 1,
                    "elements": [
                        {"type": "picture", "text": "img", "id": "pic1"},
                    ],
                },
            },
        }
        result = _analyze_accessibility(doc)
        assert result["obligations"][0]["page_number"] == 1

    def test_obligations_include_text_snippet(self) -> None:
        doc = {
            "pages": {
                "1": {
                    "page_number": 1,
                    "elements": [
                        {"type": "picture", "text": "Gráfico de barras", "id": "pic1"},
                    ],
                },
            },
        }
        result = _analyze_accessibility(doc)
        assert "Gráfico" in result["obligations"][0]["text_snippet"]