"""Contract conformance for document.structure.extract@1.

Every provider bound to this capability must satisfy these assertions. They
describe the normalized contract, never a provider's own output shape, so the
same suite proves interchangeability when a second provider appears.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization import SCHEMA_ID, validate_manifest
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter

pytestmark = pytest.mark.contract

CAPABILITY = "document.structure.extract"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "structured-document@1.json"


@pytest.fixture
def result(executor: CapabilityExecutor, sample_pdf_bytes: bytes):
    return executor.execute(
        CAPABILITY,
        sample_pdf_bytes,
        filename="contract-sample.pdf",
        media_type="application/pdf",
    )


def test_provider_reports_healthy(live_provider: ProviderDescriptor) -> None:
    health = create_adapter(live_provider).health()

    assert health.healthy
    assert health.provider == live_provider.id


def test_provider_declares_the_capability(
    live_provider: ProviderDescriptor, capabilities: CapabilityRegistry
) -> None:
    assert live_provider.implements(CAPABILITY)
    assert capabilities.get(CAPABILITY).version == 1


def test_output_validates_against_the_versioned_schema(result) -> None:
    assert validate_manifest(result.document, SCHEMA_PATH) == []


def test_output_is_the_canonical_structure_not_a_provider_shape(result) -> None:
    document = result.document

    assert document["$schema"] == SCHEMA_ID
    assert {"source", "extractor", "pages", "elements", "summary"} <= set(document)
    # Docling's own vocabulary must not surface in the contract.
    assert "json_content" not in document
    assert "texts" not in document


def test_extracted_document_has_pages_and_elements(result) -> None:
    assert result.document["summary"]["page_count"] >= 1
    assert result.document["summary"]["element_count"] >= 1
    assert result.document["elements"]


def test_elements_use_canonical_types(
    result, capabilities: CapabilityRegistry
) -> None:
    canonical = {
        "title", "heading", "paragraph", "list_item", "table", "picture",
        "formula", "code", "caption", "footnote", "page_header", "page_footer",
        "checkbox", "key_value", "form", "group", "unknown",
    }
    assert {element["type"] for element in result.document["elements"]} <= canonical


def test_pages_only_reference_known_elements(result) -> None:
    known = {element["id"] for element in result.document["elements"]}
    for page in result.document["pages"]:
        assert set(page["element_ids"]) <= known


def test_result_carries_reproducible_provenance(
    result, live_provider: ProviderDescriptor
) -> None:
    provenance = result.provenance

    assert provenance.capability == CAPABILITY
    assert provenance.provider == live_provider.id
    assert provenance.provider_version
    assert provenance.input_fingerprints and provenance.cache_key
    assert provenance.duration_ms >= 0


def test_execution_is_deterministic_for_the_same_input(
    executor: CapabilityExecutor, sample_pdf_bytes: bytes
) -> None:
    """The manifest declares this capability deterministic; verify the claim."""

    def run():
        return executor.execute(
            CAPABILITY,
            sample_pdf_bytes,
            filename="contract-sample.pdf",
            media_type="application/pdf",
        )

    first, second = run(), run()

    assert first.provenance.cache_key == second.provenance.cache_key
    assert [e["type"] for e in first.document["elements"]] == [
        e["type"] for e in second.document["elements"]
    ]
    assert [e["text"] for e in first.document["elements"]] == [
        e["text"] for e in second.document["elements"]
    ]


def test_source_metadata_describes_the_upload(result) -> None:
    source = result.document["source"]

    assert source["filename"] == "contract-sample.pdf"
    assert source["media_type"] == "application/pdf"
    assert len(source["sha256"]) == 64
