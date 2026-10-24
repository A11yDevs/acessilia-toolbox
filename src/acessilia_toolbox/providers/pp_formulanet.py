"""PP-FormulaNet math recognition adapter.

PP-FormulaNet_plus-M is PaddleOCR's formula recognition model. It runs in the
``pp-formulanet`` sidecar, which speaks the same ``/predict``, ``/health`` and
``/version`` API as ``rapid-latex-ocr``, so this adapter reuses that transport
and only changes the identity it reports.

Compared with the RapidLaTeXOCR chip model, PP-FormulaNet is a 2D-aware
encoder/decoder: matrices and vector notation (``\\vec``, ``\\nabla``) survive
recognition instead of collapsing into a single line.
"""

from __future__ import annotations

from typing import ClassVar

from acessilia_toolbox.providers.rapid_latex_ocr import RapidLatexOcrProvider


class PPFormulaNetProvider(RapidLatexOcrProvider):
    """Calls the PP-FormulaNet sidecar to extract LaTeX from formula images."""

    backend: ClassVar[str] = "pp-formulanet"


__all__ = ["PPFormulaNetProvider"]
