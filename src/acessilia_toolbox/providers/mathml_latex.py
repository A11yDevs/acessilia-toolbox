"""MathML → LaTeX reverse conversion for ``math.convert`` (``mathml-to-latex``).

MathJax only reads TeX, so the reverse direction stays a small pure-Python
mapper. It covers the constructs common in accessibility workflows (tokens,
fractions, roots, scripts, fences, named operators) and is best effort: MathML
it cannot fully convert raises ``ValueError`` instead of leaking markup into
the LaTeX.
"""

from __future__ import annotations

import re

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

# Elements the mapper understands; attributes on them are dropped.
_KNOWN_TAG_RE = re.compile(
    r"<(/?)(mi|mn|mo|mtext|msup|msub|msubsup|mfrac|msqrt|mover|munder|munderover)\b[^>]*>"
)
# Any MathML element left after conversion means it was not understood.
_LEFTOVER_TAG_RE = re.compile(r"</?(m[a-z]+|math|semantics|annotation(-xml)?)\b[^<>]*>")


def _resolve_hex_entity(match: re.Match[str]) -> str:
    cp = int(match.group(1), 16)
    return chr(cp)


def mathml_to_latex(mathml: str) -> str:
    """Convert MathML to LaTeX for common accessibility patterns.

    Raises ``ValueError`` when the input holds MathML it cannot convert.
    """
    latex = _convert(mathml)
    leftover = _LEFTOVER_TAG_RE.search(latex)
    if leftover:
        raise ValueError(f"unsupported MathML construct: {leftover.group(0)}")
    return latex


def _convert(mathml: str) -> str:
    # Pretty-printed MathML: collapse whitespace and drop it between tags.
    text = re.sub(r"\s+", " ", mathml)
    text = re.sub(r">\s+<", "><", text)
    # Attributes (mathvariant, stretchy, ...) carry no LaTeX here.
    text = _KNOWN_TAG_RE.sub(r"<\1\2>", text)
    text = re.sub(r"<mspace[^>]*/>", " ", text)

    # Strip <math> wrapper
    text = re.sub(r"</?math[^>]*>", "", text)
    # Strip <mrow> wrappers
    text = re.sub(r"</?mrow\s*/?>", "", text)
    # Strip <mstyle> wrappers
    text = re.sub(r"</?mstyle[^>]*>", "", text)
    # Unwrap <semantics> and drop its annotations
    text = re.sub(r"<annotation(-xml)?[^>]*>.*?</annotation(-xml)?>", "", text)
    text = re.sub(r"</?semantics[^>]*>", "", text)

    # Convert <mi> (identifier)
    text = re.sub(r"<mi>(.*?)</mi>", lambda m: _clean_mathml_text(m.group(1)), text)
    # Convert <mn> (number)
    text = re.sub(r"<mn>(.*?)</mn>", lambda m: m.group(1), text)
    # Convert <mtext> (text)
    text = re.sub(
        r"<mtext>(.*?)</mtext>",
        lambda m: r"\text{" + _clean_mathml_text(m.group(1)) + "}",
        text,
    )
    # Convert <mo> (operator)
    text = re.sub(
        r"<mo>(.*?)</mo>",
        lambda m: _resolve_operator(m.group(1)),
        text,
    )
    # Convert <msup> (superscript)
    text = re.sub(
        r"<msup>(.*?)</msup>",
        lambda m: "{" + _convert(m.group(1)) + "}",
        text,
    )
    # Convert <msub> (subscript)
    text = re.sub(
        r"<msub>(.*?)</msub>",
        lambda m: "{" + _convert(m.group(1)) + "}",
        text,
    )
    # Convert <msubsup>
    text = re.sub(
        r"<msubsup>(.*?)</msubsup>",
        lambda m: "{" + _convert(m.group(1)) + "}",
        text,
    )
    # Convert <mfrac> (fraction)
    text = re.sub(
        r"<mfrac>(.*?)</mfrac>",
        lambda m: r"\frac{" + _convert(m.group(1)) + "}",
        text,
    )
    # Convert <msqrt>
    text = re.sub(
        r"<msqrt>(.*?)</msqrt>",
        lambda m: r"\sqrt{" + _convert(m.group(1)) + "}",
        text,
    )
    # Convert <mover> (overscript)
    text = re.sub(
        r"<mover>(.*?)</mover>",
        lambda m: "{" + _convert(m.group(1)) + "}",
        text,
    )
    # Convert <munder>
    text = re.sub(
        r"<munder>(.*?)</munder>",
        lambda m: "{" + _convert(m.group(1)) + "}",
        text,
    )
    # Convert <munderover>
    text = re.sub(
        r"<munderover>(.*?)</munderover>",
        lambda m: "{" + _convert(m.group(1)) + "}",
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
