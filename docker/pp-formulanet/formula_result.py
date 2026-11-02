"""PaddleOCR result parsing for the pp-formulanet sidecar.

Kept apart from ``server.py`` so it can be tested without the Paddle runtime:
the only contract that tends to change between PaddleOCR releases is the shape
of the result object, and a change there must not go unnoticed.
"""

from __future__ import annotations

from typing import Any


def strip_math_delimiters(latex: str) -> str:
    """Drop a pair of delimiters wrapping the whole expression, if present.

    PP-FormulaNet normally returns bare LaTeX, but a model that echoes the
    markdown delimiters would otherwise break ``math.convert``.
    """
    for delimiter in ("$$", "$"):
        if (
            latex.startswith(delimiter)
            and latex.endswith(delimiter)
            and len(latex) > 2 * len(delimiter)
        ):
            inner = latex[len(delimiter) : -len(delimiter)]
            # ``$a$ + $b$`` is not wrapped: the inner text still carries the
            # delimiter, and stripping would corrupt the expression.
            if delimiter not in inner:
                return inner.strip()
    return latex


def recognized_latex(results: Any) -> str:
    """First non-empty ``rec_formula`` across the model results.

    PaddleOCR wraps the payload as ``{"res": {...}}``; some releases return the
    inner object directly, so both shapes are accepted. A result without a
    string ``rec_formula`` is skipped instead of ending the scan.
    """
    for result in results or []:
        data = getattr(result, "json", None)
        if not isinstance(data, dict):
            continue
        payload = data.get("res", data)
        if not isinstance(payload, dict):
            continue
        latex = payload.get("rec_formula")
        if isinstance(latex, str) and latex.strip():
            return strip_math_delimiters(latex.strip())
    return ""


__all__ = ["recognized_latex", "strip_math_delimiters"]
