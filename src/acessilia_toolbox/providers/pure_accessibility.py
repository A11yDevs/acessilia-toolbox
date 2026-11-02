"""Pure-Python accessibility marking provider.

Analyzes structured document elements and generates accessibility
obligations and annotations. Identifies missing alt text for images,
missing linearization for tables, missing verbalization for formulas,
and missing language annotation for code blocks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Obligation definitions (mirrors builder.py OBLIGATION_BY_TYPE)
# ---------------------------------------------------------------------------

_OBLIGATION_BY_TYPE: dict[str, tuple[str, str, list[str]]] = {
    "picture": (
        "describe-image",
        "The image must receive a description or be marked as decorative.",
        ["vision-description", "human-review"],
    ),
    "table": (
        "linearize-table",
        "The table must have headers and a verifiable reading order.",
        ["docling-table", "pandoc-table", "human-review"],
    ),
    "formula": (
        "verbalize-formula",
        "The formula must have an accessible mathematical representation and verbalization.",
        ["mathml", "latex-verbalizer", "human-review"],
    ),
    "code": (
        "preserve-code-semantics",
        "The code block must preserve indentation, language, and literal reading.",
        ["pandoc-code", "human-review"],
    ),
    "unknown": (
        "review-structure",
        "The unclassified element requires structural inspection.",
        ["docling-retry", "pymupdf-region", "human-review"],
    ),
}

# ---------------------------------------------------------------------------
# Accessibility analysis
# ---------------------------------------------------------------------------


def _analyze_accessibility(document: dict[str, Any]) -> dict[str, Any]:
    """Analyze a structured document for accessibility issues.

    Returns a report with:
    - obligations: list of required actions per element
    - summary: counts by obligation type
    - score: overall accessibility score (0-100)
    """
    obligations: list[dict[str, Any]] = []
    stats: dict[str, int] = {
        "total_elements": 0,
        "elements_with_issues": 0,
        "images_without_alt": 0,
        "tables_without_linearization": 0,
        "formulas_without_verbalization": 0,
        "code_without_language": 0,
        "unknown_elements": 0,
    }

    # Collect elements from pages
    pages = document.get("pages", {})
    if isinstance(pages, dict):
        pages = list(pages.values())

    for page in pages:
        page_num = page.get("page_number", 0) or page.get("page_no", 0)
        elements = page.get("elements", []) or page.get("items", []) or page.get("regions", [])

        for elem in elements:
            stats["total_elements"] += 1
            elem_type = elem.get("type", "") or elem.get("label", "")
            elem_id = elem.get("id", "") or elem.get("self_ref", "")

            obligation_info = _OBLIGATION_BY_TYPE.get(elem_type)
            if not obligation_info:
                continue

            obligation_id, rationale, methods = obligation_info

            # Check specific conditions — only flag if attribute is missing
            has_alt = bool(elem.get("alt_text") or elem.get("description"))
            has_linearization = bool(elem.get("linearized"))
            has_verbalization = bool(elem.get("verbalized") or elem.get("mathml"))
            has_language = bool(elem.get("language"))

            has_issue = False
            if elem_type == "picture" and not has_alt:
                stats["images_without_alt"] += 1
                has_issue = True
            elif elem_type == "table" and not has_linearization:
                stats["tables_without_linearization"] += 1
                has_issue = True
            elif elem_type == "formula" and not has_verbalization:
                stats["formulas_without_verbalization"] += 1
                has_issue = True
            elif elem_type == "code" and not has_language:
                stats["code_without_language"] += 1
                has_issue = True
            elif elem_type == "unknown":
                stats["unknown_elements"] += 1
                has_issue = True

            if not has_issue:
                continue

            stats["elements_with_issues"] += 1
            obligations.append({
                "element_id": elem_id,
                "element_type": elem_type,
                "page_number": page_num,
                "obligation_id": obligation_id,
                "rationale": rationale,
                "methods": methods,
                "text_snippet": (elem.get("text", "") or elem.get("content", ""))[:200],
            })

    # Calculate accessibility score
    if stats["total_elements"] > 0:
        score = max(
            0,
            100 - round((stats["elements_with_issues"] / stats["total_elements"]) * 100),
        )
    else:
        score = 100

    return {
        "obligations": obligations,
        "summary": stats,
        "score": score,
        "obligation_count": len(obligations),
    }


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PureAccessibilityProvider:
    """Pure-Python accessibility marking provider."""

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
        document = json.loads(payload.decode("utf-8", errors="replace"))
        result = _analyze_accessibility(document)

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


__all__ = ["PureAccessibilityProvider", "_analyze_accessibility"]
