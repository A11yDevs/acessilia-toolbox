"""PDDL domain fragment generation from capability manifests."""

from __future__ import annotations

from pathlib import Path

from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.pddl import capability_action, domain_fragment, predicates_list

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_DIR = PROJECT_ROOT / "capabilities"

MINIMAL = {
    "id": "document.ocr",
    "version": 1,
    "description": "Extract text from scanned documents.",
    "input": {"schema": "artifact/document@1"},
    "output": {"schema": "artifact/document-text@1"},
    "semantics": {"requires": ["scanned", "image_readable"], "produces": ["text_available"]},
}


def test_action_name_replaces_dots_with_hyphens() -> None:
    manifest = CapabilityManifest.model_validate(MINIMAL)
    action = capability_action(manifest)

    assert "document-ocr" in action
    assert "document.ocr" not in action


def test_action_includes_preconditions_and_effects() -> None:
    manifest = CapabilityManifest.model_validate(MINIMAL)
    action = capability_action(manifest)

    assert "(scanned)" in action
    assert "(image_readable)" in action
    assert "(text_available)" in action


def test_action_without_semantics_uses_defaults() -> None:
    base = {k: v for k, v in MINIMAL.items() if k != "semantics"}
    manifest = CapabilityManifest.model_validate(base)
    action = capability_action(manifest)

    assert "(document-available ?d)" in action
    assert "(processed ?d)" in action


def test_domain_fragment_is_valid_pddl_syntax() -> None:
    manifests = [CapabilityManifest.model_validate(MINIMAL)]
    fragment = domain_fragment(manifests)

    assert fragment.startswith("(define (domain acessilia-toolbox-fragment)")
    assert "(:requirements :strips)" in fragment
    assert "(:types" in fragment
    assert "(:predicates" in fragment
    assert "(:action" in fragment


def test_domain_fragment_declares_all_predicates() -> None:
    manifests = [
        CapabilityManifest.model_validate(MINIMAL),
        CapabilityManifest.model_validate(
            {
                **MINIMAL,
                "id": "table.extract",
                "semantics": {"requires": ["structured"], "produces": ["tables_available"]},
            }
        ),
    ]
    fragment = domain_fragment(manifests)

    predicates = [
        "scanned", "image_readable", "text_available",
        "structured", "tables_available",
    ]
    for predicate in predicates:
        assert predicate in fragment, f"missing predicate: {predicate}"


def test_predicates_list_covers_all_capabilities() -> None:
    registry = CapabilityRegistry([
        CapabilityManifest.model_validate(MINIMAL),
        CapabilityManifest.model_validate(
            {
                **MINIMAL,
                "id": "table.extract",
                "semantics": {"requires": ["structured"], "produces": ["tables_available"]},
            }
        ),
    ])
    predicates = predicates_list(registry)

    assert "scanned" in predicates
    assert "tables_available" in predicates
    assert predicates == sorted(predicates)


def test_shipped_manifest_produces_valid_pddl() -> None:
    registry = CapabilityRegistry.from_directory(CAPABILITIES_DIR)
    fragment = domain_fragment(registry.manifests())

    assert "(:action document-structure-extract" in fragment
    assert "(document_available)" in fragment
    assert "(structured)" in fragment
    assert "(has_ast)" in fragment
    assert "(text_available)" in fragment


def test_empty_manifest_registry_returns_stub() -> None:
    registry = CapabilityRegistry()
    fragment = domain_fragment(registry.manifests())

    assert "document-available" in fragment
    assert "(:action" not in fragment
