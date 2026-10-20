"""MathML → LaTeX reverse conversion for ``math.convert`` (``mathml-to-latex``).

MathJax only reads TeX, so the reverse direction stays a small pure-Python
mapper. It covers the constructs common in accessibility workflows (tokens,
fractions, roots, scripts, fences, named operators) and is best effort.
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


def _resolve_hex_entity(match: re.Match[str]) -> str:
    cp = int(match.group(1), 16)
    return chr(cp)


def mathml_to_latex(mathml: str) -> str:
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
        lambda m: "{" + mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <msub> (subscript)
    text = re.sub(
        r"<msub>(.*?)</msub>",
        lambda m: "{" + mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <msubsup>
    text = re.sub(
        r"<msubsup>(.*?)</msubsup>",
        lambda m: "{" + mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <mfrac> (fraction)
    text = re.sub(
        r"<mfrac>(.*?)</mfrac>",
        lambda m: r"\frac{" + mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <msqrt>
    text = re.sub(
        r"<msqrt>(.*?)</msqrt>",
        lambda m: r"\sqrt{" + mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <mover> (overscript)
    text = re.sub(
        r"<mover>(.*?)</mover>",
        lambda m: "{" + mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <munder>
    text = re.sub(
        r"<munder>(.*?)</munder>",
        lambda m: "{" + mathml_to_latex(m.group(1)) + "}",
        text,
    )
    # Convert <munderover>
    text = re.sub(
        r"<munderover>(.*?)</munderover>",
        lambda m: "{" + mathml_to_latex(m.group(1)) + "}",
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
