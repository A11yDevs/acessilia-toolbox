"""Pure-Python math providers for LaTeX conversion and verbalization.

No external ML runtime or network dependency. Handles:
- math.convert: LaTeX ↔ MathML (unidirectional via latex2mathml, reverse via simple parser)
- math.verbalize: LaTeX → natural language text in pt-BR
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

# ---------------------------------------------------------------------------
# LaTeX → MathML
# ---------------------------------------------------------------------------

try:
    from latex2mathml.converter import convert as _latex_to_mathml
except ImportError:
    _latex_to_mathml = None  # type: ignore[assignment]


def _convert_latex_to_mathml(latex: str) -> str:
    """Convert a LaTeX expression to MathML."""
    if _latex_to_mathml is None:
        raise RuntimeError("latex2mathml is not installed")
    return _latex_to_mathml(latex)


# ---------------------------------------------------------------------------
# MathML → LaTeX (simple reverse)
# ---------------------------------------------------------------------------

# Minimal mapping for reverse conversion. Covers the most common constructs
# found in accessibility workflows.
_MATHML_TO_LATEX: dict[str, str] = {
    # Named entities
    "&alpha;": r"\alpha",
    "&beta;": r"\beta",
    "&gamma;": r"\gamma",
    "&delta;": r"\delta",
    "&epsilon;": r"\epsilon",
    "&zeta;": r"\zeta",
    "&eta;": r"\eta",
    "&theta;": r"\theta",
    "&iota;": r"\iota",
    "&kappa;": r"\kappa",
    "&lambda;": r"\lambda",
    "&mu;": r"\mu",
    "&nu;": r"\nu",
    "&xi;": r"\xi",
    "&omicron;": r"o",
    "&pi;": r"\pi",
    "&rho;": r"\rho",
    "&sigma;": r"\sigma",
    "&tau;": r"\tau",
    "&upsilon;": r"\upsilon",
    "&phi;": r"\phi",
    "&chi;": r"\chi",
    "&psi;": r"\psi",
    "&omega;": r"\omega",
    "&Alpha;": r"A",
    "&Beta;": r"B",
    "&Gamma;": r"\Gamma",
    "&Delta;": r"\Delta",
    "&Theta;": r"\Theta",
    "&Lambda;": r"\Lambda",
    "&Xi;": r"\Xi",
    "&Pi;": r"\Pi",
    "&Sigma;": r"\Sigma",
    "&Phi;": r"\Phi",
    "&Psi;": r"\Psi",
    "&Omega;": r"\Omega",
    # Operators
    "&lt;": "<",
    "&gt;": ">",
    "&amp;": "&",
    "&InvisibleTimes;": " ",
    "&ApplyFunction;": "",
    "&Integral;": r"\int",
    "&Sum;": r"\sum",
    "&Prod;": r"\prod",
    "&Sqrt;": r"\sqrt",
    "&PlusMinus;": r"\pm",
    "&MinusPlus;": r"\mp",
    "&InvisibleComma;": "",
    "&VerticalLine;": "|",
    "&nabla;": r"\nabla",
    "&partial;": r"\partial",
    "&infty;": r"\infty",
    "&emptyset;": r"\emptyset",
    "&forall;": r"\forall",
    "&exist;": r"\exists",
    "&isin;": r"\in",
    "&notin;": r"\notin",
    "&sub;": r"\subset",
    "&sup;": r"\supset",
    "&sube;": r"\subseteq",
    "&supe;": r"\supseteq",
    "&cup;": r"\cup",
    "&cap;": r"\cap",
    "&times;": r"\times",
    "&divide;": r"\div",
    "&le;": r"\le",
    "&ge;": r"\ge",
    "&ne;": r"\ne",
    "&sim;": r"\sim",
    "&approx;": r"\approx",
    "&equiv;": r"\equiv",
    "&perp;": r"\perp",
    "&parallel;": r"\parallel",
    "&circ;": r"\circ",
    "&bull;": r"\bullet",
    "&rarr;": r"\rightarrow",
    "&larr;": r"\leftarrow",
    "&harr;": r"\leftrightarrow",
    "&rArr;": r"\Rightarrow",
    "&lArr;": r"\Leftarrow",
    "&hArr;": r"\Leftrightarrow",
    "&mapsto;": r"\mapsto",
    "&prime;": "'",
    "&Prime;": "''",
    "&deg;": r"^\circ",
    "&hellip;": r"\dots",
    "&minus;": "-",
    "&ast;": "*",
    "&sol;": "/",
    "&colon;": ":",
    "&comma;": ",",
    "&period;": ".",
    "&space;": " ",
    "&nbsp;": " ",
    "&thinsp;": " ",
    "&ensp;": " ",
    "&emsp;": " ",
}

# Hex entities cache
_HEX_ENTITY_RE = re.compile(r"&#x([0-9A-Fa-f]+);")


def _resolve_hex_entity(match: re.Match[str]) -> str:
    cp = int(match.group(1), 16)
    return chr(cp)


def _mathml_to_latex(mathml: str) -> str:
    """Simple MathML-to-LaTeX converter for common accessibility patterns."""
    text = mathml

    # Strip <math> wrapper
    text = re.sub(r"</?math[^>]*>", "", text)
    # Strip <mrow> wrappers
    text = re.sub(r"</?mrow\s*/?>", "", text)
    # Strip <mstyle> wrappers
    text = re.sub(r"</?mstyle[^>]*>", "", text)
    # Strip <semantics> and annotation
    text = re.sub(r"<semantics>.*?</semantics>", "", text, flags=re.DOTALL)
    text = re.sub(r"<annotation[^>]*>.*?</annotation>", "", text, flags=re.DOTALL)

    # Convert <mi> (identifier)
    text = re.sub(r"<mi>(.*?)</mi>", lambda m: _clean_mathml_text(m.group(1)), text)
    # Convert <mn> (number)
    text = re.sub(r"<mn>(.*?)</mn>", lambda m: m.group(1), text)
    # Convert <mo> (operator)
    text = re.sub(
        r"<mo>(.*?)</mo>",
        lambda m: _resolve_operator(m.group(1)),
        text,
    )
    # Convert <msup> (superscript)
    text = re.sub(
        r"<msup>(.*?)</msup>",
        lambda m: "{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <msub> (subscript)
    text = re.sub(
        r"<msub>(.*?)</msub>",
        lambda m: "{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <msubsup>
    text = re.sub(
        r"<msubsup>(.*?)</msubsup>",
        lambda m: "{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <mfrac> (fraction)
    text = re.sub(
        r"<mfrac>(.*?)</mfrac>",
        lambda m: r"\frac{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <msqrt>
    text = re.sub(
        r"<msqrt>(.*?)</msqrt>",
        lambda m: r"\sqrt{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <mover> (overscript)
    text = re.sub(
        r"<mover>(.*?)</mover>",
        lambda m: "{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <munder>
    text = re.sub(
        r"<munder>(.*?)</munder>",
        lambda m: "{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <munderover>
    text = re.sub(
        r"<munderover>(.*?)</munderover>",
        lambda m: "{" + _mathml_to_latex(m.group(1)) + "}",
        text,
    )

    # Resolve hex entities
    text = _HEX_ENTITY_RE.sub(_resolve_hex_entity, text)
    # Resolve named entities
    for entity, latex in _MATHML_TO_LATEX.items():
        text = text.replace(entity, latex)

    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_mathml_text(text: str) -> str:
    """Decode entities in MathML text content."""
    text = _HEX_ENTITY_RE.sub(_resolve_hex_entity, text)
    for entity, latex in _MATHML_TO_LATEX.items():
        text = text.replace(entity, latex)
    return text


def _resolve_operator(entity_or_char: str) -> str:
    """Resolve a MathML operator to its LaTeX equivalent."""
    decoded = entity_or_char
    decoded = _HEX_ENTITY_RE.sub(_resolve_hex_entity, decoded)
    for entity, latex in _MATHML_TO_LATEX.items():
        decoded = decoded.replace(entity, latex)
    return decoded


# ---------------------------------------------------------------------------
# LaTeX verbalizer (pt-BR)
# ---------------------------------------------------------------------------

_LATEX_VERBALIZE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\\frac\{([^}]*)\}\{([^}]*)\}"), r"\1 sobre \2"),
    (re.compile(r"\\sqrt(?:\[([^\]]*)\])?\{([^}]*)\}"), r"raiz quadrada de \2"),
    (re.compile(r"\\int"), "integral"),
    (re.compile(r"\\sum"), "somatório"),
    (re.compile(r"\\prod"), "produtório"),
    (re.compile(r"\\lim"), "limite"),
    (re.compile(r"\\infty"), "infinito"),
    (re.compile(r"\\partial"), "derivada parcial"),
    (re.compile(r"\\nabla"), "nabla"),
    (re.compile(r"\\alpha"), "alfa"),
    (re.compile(r"\\beta"), "beta"),
    (re.compile(r"\\gamma"), "gama"),
    (re.compile(r"\\delta"), "delta"),
    (re.compile(r"\\epsilon"), "épsilon"),
    (re.compile(r"\\zeta"), "zeta"),
    (re.compile(r"\\eta"), "eta"),
    (re.compile(r"\\theta"), "teta"),
    (re.compile(r"\\iota"), "iota"),
    (re.compile(r"\\kappa"), "capa"),
    (re.compile(r"\\lambda"), "lambda"),
    (re.compile(r"\\mu"), "mu"),
    (re.compile(r"\\nu"), "nu"),
    (re.compile(r"\\xi"), "csi"),
    (re.compile(r"\\omicron"), "omicron"),
    (re.compile(r"\\pi"), "pi"),
    (re.compile(r"\\rho"), "rô"),
    (re.compile(r"\\sigma"), "sigma"),
    (re.compile(r"\\tau"), "tau"),
    (re.compile(r"\\upsilon"), "upsilon"),
    (re.compile(r"\\phi"), "fi"),
    (re.compile(r"\\chi"), "qui"),
    (re.compile(r"\\psi"), "psi"),
    (re.compile(r"\\omega"), "ômega"),
    (re.compile(r"\\Gamma"), "Gama"),
    (re.compile(r"\\Delta"), "Delta"),
    (re.compile(r"\\Theta"), "Teta"),
    (re.compile(r"\\Lambda"), "Lambda"),
    (re.compile(r"\\Xi"), "Csi"),
    (re.compile(r"\\Pi"), "Pi"),
    (re.compile(r"\\Sigma"), "Sigma"),
    (re.compile(r"\\Phi"), "Fi"),
    (re.compile(r"\\Psi"), "Psi"),
    (re.compile(r"\\Omega"), "Ômega"),
    (re.compile(r"\\pm"), "mais ou menos"),
    (re.compile(r"\\mp"), "menos ou mais"),
    (re.compile(r"\\times"), "vezes"),
    (re.compile(r"\\div"), "dividido por"),
    (re.compile(r"\\cdot"), "vezes"),
    (re.compile(r"\\leq|\\le"), "menor ou igual"),
    (re.compile(r"\\geq|\\ge"), "maior ou igual"),
    (re.compile(r"\\neq|\\ne"), "diferente de"),
    (re.compile(r"\\approx"), "aproximadamente"),
    (re.compile(r"\\equiv"), "equivalente a"),
    (re.compile(r"\\sim"), "similar a"),
    (re.compile(r"\\subset"), "subconjunto de"),
    (re.compile(r"\\supset"), "superconjunto de"),
    (re.compile(r"\\subseteq"), "subconjunto ou igual"),
    (re.compile(r"\\supseteq"), "superconjunto ou igual"),
    (re.compile(r"\\cup"), "união"),
    (re.compile(r"\\cap"), "interseção"),
    (re.compile(r"\\in"), "pertence a"),
    (re.compile(r"\\notin"), "não pertence a"),
    (re.compile(r"\\forall"), "para todo"),
    (re.compile(r"\\exists"), "existe"),
    (re.compile(r"\\rightarrow|\\to"), "seta para a direita"),
    (re.compile(r"\\leftarrow"), "seta para a esquerda"),
    (re.compile(r"\\Rightarrow"), "implica"),
    (re.compile(r"\\Leftrightarrow"), "se e somente se"),
    (re.compile(r"\\mapsto"), "mapeado para"),
    (re.compile(r"\\perp"), "perpendicular"),
    (re.compile(r"\\parallel"), "paralelo"),
    (re.compile(r"\\circ"), "graus"),
    (re.compile(r"\\bullet"), "ponto"),
    (re.compile(r"\\dots"), "reticências"),
    (re.compile(r"\\colon"), "dois pontos"),
    (re.compile(r"\\vert\b"), "tal que"),
    (re.compile(r"\\\|"), "tal que"),
    (re.compile(r"\^"), "elevado a"),
    (re.compile(r"_"), "subscrito"),
    (re.compile(r"\{"), ""),
    (re.compile(r"\}"), ""),
    (re.compile(r"\\text\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\mathrm\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\mathbf\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\mathit\{([^}]*)\}"), r"\1"),
]


def _verbalize_latex(latex: str) -> str:
    """Convert a LaTeX expression to spoken Portuguese text."""
    text = latex.strip()

    # Remove display math delimiters
    text = re.sub(r"^\$\$|\$\$$", "", text)
    text = re.sub(r"^\\\[|\\\]$", "", text)
    text = re.sub(r"^\(|\)$", "", text)

    # Apply verbalization patterns
    for pattern, replacement in _LATEX_VERBALIZE_PATTERNS:
        text = pattern.sub(replacement, text)

    # Clean up multiple spaces and punctuation artifacts
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,;:.!?])", r"\1", text)

    return text


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

VERSION = "1.0.0"


class PureMathProvider:
    """Pure-Python math provider for conversion and verbalization."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        params = dict(parameters or {})
        latex = payload.decode("utf-8", errors="replace").strip()

        if capability_id == "math.convert":
            direction = params.get("direction", "latex-to-mathml")
            result = self._convert(latex, direction)
        elif capability_id == "math.verbalize":
            result = self._verbalize(latex)
        else:
            raise ValueError(f"Unsupported capability: {capability_id}")

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=result,
            backend="pure-python",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=VERSION,
            configuration={
                "capability": capability_id,
                **params,
            },
        )

    def versions(self) -> dict[str, str]:
        return {"provider": VERSION}

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            version=VERSION,
            checked_at=datetime.now(UTC),
        )

    def _convert(
        self, latex: str, direction: str
    ) -> dict[str, Any]:
        if direction == "latex-to-mathml":
            mathml = _convert_latex_to_mathml(latex)
            return {
                "latex": latex,
                "mathml": mathml,
                "direction": direction,
            }
        elif direction == "mathml-to-latex":
            converted = _mathml_to_latex(latex)
            return {
                "latex": converted,
                "mathml": latex,
                "direction": direction,
            }
        else:
            raise ValueError(f"Unknown conversion direction: {direction}")

    def _verbalize(self, latex: str) -> dict[str, Any]:
        verbalized = _verbalize_latex(latex)
        return {
            "latex": latex,
            "verbalized": verbalized,
            "language": "pt-BR",
        }


__all__ = ["PureMathProvider", "_convert_latex_to_mathml", "_verbalize_latex"]
