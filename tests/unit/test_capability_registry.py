"""Capability manifest parsing and registry behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from acessilia_toolbox.core.capability import (
    CapabilityManifest,
    CapabilityRegistry,
    load_manifest,
)
from acessilia_toolbox.core.errors import CapabilityNotFoundError, ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_DIR = PROJECT_ROOT / "capabilities"

MINIMAL = {
    "id": "document.ocr",
    "version": 1,
    "description": "Extract machine-readable text.",
    "input": {"schema": "artifact/document@1"},
    "output": {"schema": "artifact/document-text@1"},
}


def manifest(**overrides: object) -> CapabilityManifest:
    return CapabilityManifest.model_validate({**MINIMAL, **overrides})


def test_manifest_defaults_are_deterministic_and_cacheable() -> None:
    parsed = manifest()
    assert parsed.execution.deterministic
    assert parsed.execution.idempotent
    assert parsed.execution.cacheable
    assert parsed.key == "document.ocr@1"


def test_schema_field_is_readable_under_its_alias() -> None:
    parsed = manifest()
    assert parsed.input.schema_ref == "artifact/document@1"
    assert parsed.model_dump(by_alias=True)["input"]["schema"] == "artifact/document@1"


@pytest.mark.parametrize(
    "capability_id",
    ["docling", "Document.OCR", "document..ocr", "document.ocr.", "document_ocr", ""],
)
def test_rejects_non_hierarchical_identifiers(capability_id: str) -> None:
    with pytest.raises(ValueError):
        manifest(id=capability_id)


def test_rejects_unknown_manifest_fields() -> None:
    with pytest.raises(ValueError):
        manifest(backend="docling")


def test_rejects_invalid_pddl_predicates() -> None:
    with pytest.raises(ValueError):
        manifest(semantics={"requires": ["Image Readable"], "produces": []})


def test_accepts_predicate_names_with_underscores() -> None:
    parsed = manifest(semantics={"requires": ["image_readable"], "produces": ["text_available"]})
    assert parsed.semantics.requires == ["image_readable"]


def test_registry_rejects_duplicate_capability_versions() -> None:
    registry = CapabilityRegistry([manifest()])
    with pytest.raises(ConfigurationError):
        registry.register(manifest())


def test_registry_returns_highest_version_when_unspecified() -> None:
    registry = CapabilityRegistry([manifest(), manifest(version=3), manifest(version=2)])
    assert registry.get("document.ocr").version == 3
    assert registry.get("document.ocr", version=2).version == 2


def test_registry_raises_for_unknown_capability() -> None:
    registry = CapabilityRegistry([manifest()])
    with pytest.raises(CapabilityNotFoundError):
        registry.get("table.extract")
    with pytest.raises(CapabilityNotFoundError):
        registry.get("document.ocr", version=9)


def test_registry_listing_is_sorted_and_countable() -> None:
    registry = CapabilityRegistry([manifest(version=2), manifest(id="table.extract")])
    assert [m.key for m in registry.manifests()] == ["document.ocr@2", "table.extract@1"]
    assert registry.ids() == ["document.ocr", "table.extract"]
    assert len(registry) == 2
    assert "document.ocr@2" in registry


def test_load_manifest_reports_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_manifest(path)


def test_load_manifest_rejects_non_mapping_documents(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- document.ocr\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_manifest(path)


def test_from_directory_reports_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        CapabilityRegistry.from_directory(tmp_path / "absent")


def test_shipped_capability_manifests_are_valid() -> None:
    registry = CapabilityRegistry.from_directory(CAPABILITIES_DIR)
    assert "document.structure.extract@1" in registry

    extract = registry.get("document.structure.extract")
    assert extract.output.schema_ref == "artifact/structured-document@1"
    assert "structured" in extract.semantics.produces
    assert [binding.id for binding in extract.providers] == ["docling"]


def test_shipped_manifests_never_name_a_provider_in_the_capability_id() -> None:
    registry = CapabilityRegistry.from_directory(CAPABILITIES_DIR)
    provider_names = {
        binding.id for m in registry.manifests() for binding in m.providers
    }
    for capability_id in registry.ids():
        segments = set(capability_id.split("."))
        assert not (segments & provider_names), capability_id
