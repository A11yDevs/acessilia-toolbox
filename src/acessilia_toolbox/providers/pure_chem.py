"""Pure-Python chemistry provider: LaTeX mhchem normalization.

Backs ``chem.convert`` — converts a LaTeX expression using the ``\\ce{}``
macro (mhchem syntax) into:

- a structured form (reactants / products / conditions), and
- a human-readable pt-BR textual rendering for verbalization.

No network, no ML runtime. The mhchem syntax subset covered covers the
common cases in accessibility workflows: formulas, stoichiometric
coefficients, charges, states of matter, arrows (->, <=>), reaction
conditions above arrows, plus/spacing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.errors import ProviderExecutionError
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

VERSION = "1.0.0"

# Charges: Na^{+}, Na^+, or bare trailing Na+ / Cl- at the end of a term.
_CHARGE_RE = re.compile(r"\^\{?(\d*[+-])\}?")
_BARE_CHARGE_RE = re.compile(r"(?:^|(?<=[A-Za-z0-9\)]))(\d*[+-])(?=\s*\(|$)")

# Textual renderings for mhchem arrows and common operators.
_ARROW_TEXT = {
    "->": "reage para formar",
    "<->": "está em equilíbrio com",
    "<=>": "está em equilíbrio com",
    "<=>T[": "está em equilíbrio, sob condições, com",
    "->T[": "reage, sob condições, para formar",
    "<-": "forma-se a partir de",
}

_STATE_RE = re.compile(r"\^?\s*\((s|l|g|aq)\)")


def _strip_dollars(latex: str) -> str:
    """Remove surrounding $...$ / \\[ ... \\] / \\ce wrapper."""
    text = latex.strip()
    text = re.sub(r"^\\\[|\\\]$", "", text).strip()
    text = text.strip("$").strip()
    match = re.match(r"^\\ce\{(.*)\}$", text, flags=re.DOTALL)
    if match:
        text = match.group(1)
    return text.strip()


def _split_arrow(body: str) -> tuple[str, str | None, str | None]:
    """Split at the first reaction arrow; capture condition ``T[...]``."""
    arrow_match = re.search(r"(<=>|->|<-)(T\[[^\]]*\])?", body)
    if not arrow_match:
        return body, None, None
    condition = (arrow_match.group(2) or "")
    condition = condition.removeprefix("T[").removesuffix("]")
    left = body[: arrow_match.start()]
    right = body[arrow_match.end():]
    return left, right, (condition or None)


def _split_terms(side: str) -> list[str]:
    """Split a reaction side on '+' used as a term separator.

    mhchem separates terms with `` + `` (spaces around the plus). A plus
    glued to the preceding token is an ionic charge (``Na+``) and does not
    split.
    """
    terms: list[str] = []
    depth = 0
    current: list[str] = []
    for index, char in enumerate(side):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if (
            char == "+"
            and depth == 0
            and index > 0
            and side[index - 1] == " "
            and index + 1 < len(side)
            and side[index + 1] == " "
        ):
            terms.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        terms.append(tail)
    return [term for term in terms if term]


def _formula_parts(term: str) -> dict[str, Any]:
    """Break one term into coefficient / formula / charge / state."""
    text = term.strip()
    coefficient: str | None = None
    coefficient_match = re.match(r"^(\d+)\s*(.*)$", text)
    if coefficient_match:
        coefficient = coefficient_match.group(1)
        text = coefficient_match.group(2)

    charge: str | None = None
    charge_match = _CHARGE_RE.search(text)
    if charge_match:
        charge = charge_match.group(1)
        text = text[: charge_match.start()] + text[charge_match.end():]
    else:
        bare_match = _BARE_CHARGE_RE.search(text)
        if bare_match:
            charge = bare_match.group(1)
            text = text[: bare_match.start()] + text[bare_match.end():]

    state: str | None = None
    state_match = _STATE_RE.search(text)
    if state_match:
        state = state_match.group(1)
        text = text[: state_match.start()] + text[state_match.end():]

    return {
        "coefficient": coefficient,
        "formula": text.strip(),
        "charge": charge,
        "state": state,
    }


_STATE_TEXT = {"s": "sólido", "l": "líquido", "g": "gasoso", "aq": "aquoso"}


def _term_text(term: dict[str, Any]) -> str:
    pieces: list[str] = []
    if term.get("coefficient"):
        pieces.append(f"{term['coefficient']} ")
    pieces.append(str(term.get("formula", "")))
    if term.get("charge"):
        pieces.append(f" com carga {term['charge']}")
    if term.get("state"):
        pieces.append(f" ({_STATE_TEXT[term['state']]})")
    return "".join(pieces).strip()


def normalize_mhchem(latex: str) -> dict[str, Any]:
    """Normalize a ``\\ce{}`` expression into a structured reaction."""
    body = _strip_dollars(latex)
    if not body:
        raise ProviderExecutionError(
            "empty mhchem expression", provider="pure-chem"
        )

    left, right, condition = _split_arrow(body)
    if right is None:
        # Not a reaction: a single formula/species.
        term = _formula_parts(left)
        return {
            "kind": "formula",
            "latex": body,
            "formula": term["formula"],
            "coefficient": term["coefficient"],
            "charge": term["charge"],
            "state": term["state"],
            "text": _term_text(term),
        }

    arrow = "<=>"
    arrow_match = re.search(r"(<=>|->|<-)", body)
    if arrow_match:
        arrow = arrow_match.group(1)

    reactants = [_formula_parts(t) for t in _split_terms(left)]
    products = [_formula_parts(t) for t in _split_terms(right)]

    arrow_text = _ARROW_TEXT.get(arrow, "reage para formar")
    text_parts = [
        " + ".join(_term_text(t) for t in reactants),
        arrow_text,
    ]
    if condition:
        text_parts.append(f"sob {condition},")
    text_parts.append(" + ".join(_term_text(t) for t in products))

    return {
        "kind": "reaction",
        "latex": body,
        "reactants": reactants,
        "products": products,
        "arrow": arrow,
        "conditions": condition,
        "text": " ".join(text_parts),
    }


class PureChemProvider:
    """Pure-Python chemistry provider for mhchem normalization."""

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
        if capability_id not in ("chem.convert", "math.convert"):
            raise ValueError(f"Unsupported capability: {capability_id}")

        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        latex = payload.decode("utf-8", errors="replace").strip()
        result = normalize_mhchem(latex)

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
                **dict(parameters or {}),
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


__all__ = ["PureChemProvider", "normalize_mhchem"]
