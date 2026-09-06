"""Snapshot comparison.

Strips volatile fields — timestamps, paths, digests, generated IDs — so the
comparison covers the stable semantic structure and not run-to-run noise.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

VOLATILE_ROOT_FIELDS = {
    "manifest_id",
    "revision",
    "status",
    "created_at",
    "$schema",
    "schema_version",
}

VOLATILE_ELEMENT_FIELDS = {
    "id",
    "source_ref",
    "parent_ref",
    "parent_id",
    "confidence",
}

VOLATILE_EXTRACTOR_FIELDS = {
    "version",
    "started_at",
    "completed_at",
    "duration_ms",
}

VOLATILE_SOURCE_FIELDS = {
    "document_id",
    "path",
    "sha256",
    "byte_size",
}

# Metadata worth comparing; anything else tracks provider internals.
STABLE_METADATA_FIELDS = (
    "table_ast",
    "table_row_count",
    "table_column_count",
    "table_has_header",
    "table_linearization_hint",
    "enumerated",
    "marker",
    "content_layer",
    "docling_class",
)

COMPARED_ELEMENT_FIELDS = (
    "type",
    "raw_label",
    "hierarchy_level",
    "reading_order",
    "page_number",
)


def normalize(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a copy without volatile fields."""
    copy: dict[str, Any] = json.loads(json.dumps(manifest))

    _strip(copy, VOLATILE_ROOT_FIELDS)
    _strip(copy.get("source", {}), VOLATILE_SOURCE_FIELDS)
    _strip(copy.get("extractor", {}), VOLATILE_EXTRACTOR_FIELDS)

    for element in copy.get("elements", []):
        _strip(element, VOLATILE_ELEMENT_FIELDS)
        # Absolute coordinates shift with any layout change.
        element.pop("provenance", None)
        if "metadata" in element:
            element["metadata"] = {
                key: element["metadata"][key]
                for key in STABLE_METADATA_FIELDS
                if key in element["metadata"]
            }

    for page in copy.get("pages", []):
        page.pop("element_ids", None)
    for observation in copy.get("observations", []):
        observation.pop("id", None)
    for obligation in copy.get("obligations", []):
        obligation.pop("id", None)
        obligation.pop("target_ids", None)

    return copy


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Report semantic differences between two structured documents."""
    left, right = normalize(expected), normalize(actual)
    diffs: list[str] = []

    checks: list[tuple[str, Callable[[dict[str, Any]], Any]]] = [
        ("title", lambda m: m.get("title")),
        ("language", lambda m: m.get("language")),
        ("page_count", lambda m: len(m.get("pages", []))),
        ("element_count", lambda m: len(m.get("elements", []))),
        ("observation_count", lambda m: len(m.get("observations", []))),
        ("obligation_count", lambda m: len(m.get("obligations", []))),
    ]
    for name, accessor in checks:
        if accessor(left) != accessor(right):
            diffs.append(f"{name}: expected={accessor(left)}, got={accessor(right)}")

    if left.get("summary") != right.get("summary"):
        diffs.append(
            f"summary: expected={left.get('summary')}, got={right.get('summary')}"
        )

    for index, (expected_el, actual_el) in enumerate(
        zip(left.get("elements", []), right.get("elements", []), strict=False)
    ):
        for key in COMPARED_ELEMENT_FIELDS:
            if expected_el.get(key) != actual_el.get(key):
                diffs.append(
                    f"elements[{index}].{key}: "
                    f"expected={expected_el.get(key)}, got={actual_el.get(key)}"
                )

    for index, (expected_ob, actual_ob) in enumerate(
        zip(left.get("obligations", []), right.get("obligations", []), strict=False)
    ):
        for key in ("kind", "rationale"):
            if expected_ob.get(key) != actual_ob.get(key):
                diffs.append(
                    f"obligations[{index}].{key}: "
                    f"expected={expected_ob.get(key)}, got={actual_ob.get(key)}"
                )

    return diffs


def compare_files(expected_path: Path, actual_path: Path) -> list[str]:
    return compare(
        json.loads(expected_path.read_text(encoding="utf-8")),
        json.loads(actual_path.read_text(encoding="utf-8")),
    )


def _strip(node: dict[str, Any], keys: set[str]) -> None:
    for key in keys & node.keys():
        del node[key]
