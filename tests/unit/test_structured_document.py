"""Normalization into the canonical structured document.

Ported from acessilia-structure-extractor; the Processing Manifest 1.1.0
contract is preserved so existing consumers keep working.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.fixtures.documents import (
    FakeDocument,
    bbox,
    body_root,
    extraction_of,
    item,
    provenance,
    sample_pdf,
)

from acessilia_toolbox.core.normalization.builder import build_processing_manifest
from acessilia_toolbox.core.normalization.models import SCHEMA_ID
from acessilia_toolbox.core.normalization.schema import (
    processing_manifest_schema,
    validate_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "structured-document@1.json"


def build(tmp_path: Path, document: FakeDocument | None = None):
    source = sample_pdf(tmp_path)
    return source, build_processing_manifest(
        source, extraction_of(document or FakeDocument())
    )


def test_builds_valid_manifest_and_candidate_obligation(tmp_path: Path) -> None:
    _, manifest = build(tmp_path)
    payload = manifest.model_dump(mode="json", by_alias=True)

    assert payload["$schema"] == SCHEMA_ID
    assert manifest.summary.page_count == 1
    assert manifest.summary.element_count == 3
    assert manifest.elements[1].parent_id == manifest.elements[0].id
    assert manifest.obligations[0].kind == "describe-image"
    assert manifest.obligations[0].selected is False
    assert validate_manifest(payload, SCHEMA_PATH) == []


def test_schema_version_is_unchanged_by_the_migration() -> None:
    """The 1.1.0 contract is preserved so Acessilia consumers keep working."""
    assert SCHEMA_ID == "urn:a11y-devs:schema:processing-manifest:1.1.0"


def test_generated_schema_matches_the_versioned_file() -> None:
    schema = processing_manifest_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == SCHEMA_ID
    assert schema["title"] == "ProcessingManifest"
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == schema


def test_rejects_references_to_unknown_elements(tmp_path: Path) -> None:
    _, manifest = build(tmp_path)
    payload = manifest.model_dump(mode="json", by_alias=True)
    payload["obligations"][0]["target_ids"] = ["element-missing"]

    errors = validate_manifest(payload)

    assert any("references unknown targets" in error for error in errors)


def test_preserves_code_text_verbatim(tmp_path: Path) -> None:
    document = FakeDocument(
        [
            body_root(),
            item(
                "code",
                "#/codes/0",
                text="def demo():\n    return 42\n",
                prov=[provenance(box=bbox(80, 120, 520, 260), charspan=(0, 24))],
            ),
        ],
        width=600,
        height=800,
    )
    _, manifest = build(tmp_path, document)

    code_elements = [e for e in manifest.elements if e.type == "code"]
    assert code_elements
    assert "return 42" in (code_elements[0].text or "")


def test_extracts_table_ast_metadata(tmp_path: Path) -> None:
    document = FakeDocument(
        [
            body_root(),
            item(
                "table",
                "#/tables/0",
                text="",
                rows=[["Name", "Value"], ["Rate", "10%"]],
                table={
                    "caption": "Summary",
                    "header": [
                        {
                            "cells": [
                                {"text": "Name", "scope": "col"},
                                {"text": "Value", "scope": "col"},
                            ]
                        }
                    ],
                    "body": [{"cells": [{"text": "Rate"}, {"text": "10%"}]}],
                },
                prov=[provenance(box=bbox(50, 100, 550, 200), charspan=None)],
            ),
        ],
        width=600,
        height=800,
    )
    _, manifest = build(tmp_path, document)

    table_elements = [e for e in manifest.elements if e.type == "table"]
    assert table_elements
    assert table_elements[0].metadata


def test_table_elements_carry_a_linearization_obligation(tmp_path: Path) -> None:
    document = FakeDocument(
        [
            body_root(),
            item(
                "table",
                "#/tables/0",
                text="",
                rows=[["Name", "Value"]],
                prov=[provenance(charspan=None)],
            ),
        ]
    )
    _, manifest = build(tmp_path, document)

    kinds = {obligation.kind for obligation in manifest.obligations}
    assert "linearize-table" in kinds


def test_source_document_records_content_digest(tmp_path: Path) -> None:
    source, manifest = build(tmp_path)

    assert manifest.source.filename == source.name
    assert len(manifest.source.sha256) == 64
    assert manifest.source.byte_size == source.stat().st_size


@pytest.mark.parametrize("language", ["pt-BR", "en-US"])
def test_language_is_carried_into_the_manifest(tmp_path: Path, language: str) -> None:
    source = sample_pdf(tmp_path)
    manifest = build_processing_manifest(
        source, extraction_of(FakeDocument()), language=language
    )
    assert manifest.language == language


def test_extractor_run_records_provider_identity(tmp_path: Path) -> None:
    _, manifest = build(tmp_path)

    assert manifest.extractor.name == "docling"
    assert manifest.extractor.version == "2.test"
    assert manifest.extractor.duration_ms == 5


def test_summary_counts_match_the_collections(tmp_path: Path) -> None:
    _, manifest = build(tmp_path)

    assert manifest.summary.element_count == len(manifest.elements)
    assert manifest.summary.page_count == len(manifest.pages)
    assert manifest.summary.obligation_count == len(manifest.obligations)
    assert manifest.summary.observation_count == len(manifest.observations)


def test_pages_reference_only_known_elements(tmp_path: Path) -> None:
    _, manifest = build(tmp_path)
    known = {element.id for element in manifest.elements}

    for page in manifest.pages:
        assert set(page.element_ids) <= known


def test_document_without_items_still_validates(tmp_path: Path) -> None:
    _, manifest = build(tmp_path, FakeDocument([body_root()]))
    payload = manifest.model_dump(mode="json", by_alias=True)

    assert validate_manifest(payload, SCHEMA_PATH) == []


def test_unknown_labels_fall_back_to_the_unknown_type(tmp_path: Path) -> None:
    document = FakeDocument(
        [body_root(), item("wingding", "#/texts/0", text="?", prov=[provenance()])]
    )
    _, manifest = build(tmp_path, document)

    assert any(element.type == "unknown" for element in manifest.elements)


def test_iterate_items_receives_a_document_shaped_object() -> None:
    document = FakeDocument()
    first, level = next(iter(document.iterate_items()))

    assert isinstance(first, SimpleNamespace)
    assert level == 0
