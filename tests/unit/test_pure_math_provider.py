"""pure-math provider behavior for LaTeX conversion and verbalization."""

from __future__ import annotations

import pytest

from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pure_math import (
    PureMathProvider,
    _verbalize_latex,
)


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "pure-math",
        "version": "1.0",
        "transport": "in_process",
        "capabilities": ["math.convert", "math.verbalize"],
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def extract(
    adapter: PureMathProvider,
    capability: str,
    latex: str,
    **params: object,
):
    return adapter.execute(
        capability,
        latex.encode("utf-8"),
        filename="expression.txt",
        media_type="text/plain",
        parameters=params if params else None,
    )


class TestPureMathProvider:
    def test_latex_to_mathml_conversion(self) -> None:
        adapter = PureMathProvider(descriptor())
        result = extract(adapter, "math.convert", "E = mc^2",
                         direction="latex-to-mathml")

        assert result.backend == "pure-python"
        assert result.document["direction"] == "latex-to-mathml"
        assert result.document["latex"] == "E = mc^2"
        assert "<math " in result.document["mathml"]
        assert "<mi>E</mi>" in result.document["mathml"]

    def test_latex_to_mathml_with_fraction(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"\frac{-b \pm \sqrt{b^2 - 4ac}}{2a}"
        result = extract(adapter, "math.convert", latex,
                         direction="latex-to-mathml")

        assert "<mfrac>" in result.document["mathml"]
        assert "<msqrt>" in result.document["mathml"]

    def test_latex_to_mathml_with_integral(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"\int_{0}^{\infty} e^{-x^2} dx"
        result = extract(adapter, "math.convert", latex,
                         direction="latex-to-mathml")

        assert "<mo>&#x0222B;" in result.document["mathml"] or "∫" in result.document["mathml"]

    def test_verbalize_simple_expression(self) -> None:
        adapter = PureMathProvider(descriptor())
        result = extract(adapter, "math.verbalize", "E = mc^2")

        assert result.document["language"] == "pt-BR"
        assert result.document["latex"] == "E = mc^2"
        assert isinstance(result.document["verbalized"], str)

    def test_verbalize_fraction(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"\frac{1}{2}"
        result = extract(adapter, "math.verbalize", latex)

        verbalized = result.document["verbalized"]
        assert "sobre" in verbalized.lower() or "1" in verbalized

    def test_verbalize_square_root(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"\sqrt{2}"
        result = extract(adapter, "math.verbalize", latex)

        verbalized = result.document["verbalized"]
        assert "raiz" in verbalized.lower()

    def test_verbalize_greek_letters(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"\alpha + \beta = \gamma"
        result = extract(adapter, "math.verbalize", latex)

        verbalized = result.document["verbalized"]
        assert "alfa" in verbalized
        assert "beta" in verbalized
        assert "gama" in verbalized

    def test_verbalize_operators(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"a \leq b \geq c \neq d"
        result = extract(adapter, "math.verbalize", latex)

        verbalized = result.document["verbalized"]
        assert "menor ou igual" in verbalized
        assert "maior ou igual" in verbalized
        assert "diferente" in verbalized

    def test_verbalize_set_notation(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"x \in A \cup B"
        result = extract(adapter, "math.verbalize", latex)

        verbalized = result.document["verbalized"]
        assert "pertence" in verbalized
        assert "união" in verbalized or "uniao" in verbalized

    def test_verbalize_limits(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"\lim_{x \to \infty} f(x)"
        result = extract(adapter, "math.verbalize", latex)

        verbalized = result.document["verbalized"]
        assert "limite" in verbalized
        assert "infinito" in verbalized

    def test_health_returns_healthy(self) -> None:
        adapter = PureMathProvider(descriptor())
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "pure-math"

    def test_versions_returns_version(self) -> None:
        adapter = PureMathProvider(descriptor())
        versions = adapter.versions()
        assert "provider" in versions

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, PureMathProvider)

    def test_unknown_capability_raises_error(self) -> None:
        adapter = PureMathProvider(descriptor())
        with pytest.raises(ValueError, match="Unsupported capability"):
            extract(adapter, "unknown.capability", "test")

    def test_unknown_direction_raises_error(self) -> None:
        adapter = PureMathProvider(descriptor())
        with pytest.raises(ValueError, match="Unknown conversion direction"):
            extract(adapter, "math.convert", "test",
                    direction="invalid-direction")

    def test_verbalize_empty_latex(self) -> None:
        adapter = PureMathProvider(descriptor())
        result = extract(adapter, "math.verbalize", "")
        assert result.document["verbalized"] == ""

    def test_verbalize_text_command(self) -> None:
        adapter = PureMathProvider(descriptor())
        latex = r"\text{hello world}"
        result = extract(adapter, "math.verbalize", latex)
        assert "hello world" in result.document["verbalized"]


class TestVerbalizeLatex:
    """Unit tests for the _verbalize_latex helper function."""

    def test_verbalize_fraction(self) -> None:
        assert "sobre" in _verbalize_latex(r"\frac{a}{b}")

    def test_verbalize_sqrt(self) -> None:
        assert "raiz" in _verbalize_latex(r"\sqrt{x}")

    def test_verbalize_greek(self) -> None:
        assert "alfa" in _verbalize_latex(r"\alpha")

    def test_verbalize_operators(self) -> None:
        assert "mais ou menos" in _verbalize_latex(r"\pm")

    def test_verbalize_relations(self) -> None:
        assert "menor ou igual" in _verbalize_latex(r"\leq")

    def test_verbalize_set(self) -> None:
        assert "pertence" in _verbalize_latex(r"\in")

    def test_verbalize_arrows(self) -> None:
        assert "implica" in _verbalize_latex(r"\Rightarrow")

    def test_verbalize_cleanup(self) -> None:
        result = _verbalize_latex(r"  E = mc^2  ")
        assert result == result.strip()

    def test_verbalize_display_math_delimiters(self) -> None:
        result = _verbalize_latex(r"$$E = mc^2$$")
        assert "E = mc" in result
        assert "elevado a" in result

    def test_verbalize_text_mode(self) -> None:
        result = _verbalize_latex(r"\text{hello}")
        assert "hello" in result
