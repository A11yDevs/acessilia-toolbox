"""Provider version changes must invalidate cached results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tests.fixtures.documents import FakeDocument

from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry

MANIFEST = {
    "id": "document.structure.extract",
    "version": 1,
    "description": "Extract document structure.",
    "input": {"schema": "artifact/document@1"},
    "output": {"schema": "artifact/structured-document@1"},
}


class VersionedProvider:
    def __init__(self, descriptor: ProviderDescriptor, components: dict[str, str]) -> None:
        self.descriptor = descriptor
        self._components = components

    def execute(self, capability_id: str, payload: bytes, **_: Any) -> ExtractionResult:
        timestamp = datetime(2026, 9, 6, tzinfo=UTC)
        return ExtractionResult(
            document=FakeDocument(),
            backend="docling",
            started_at=timestamp,
            completed_at=timestamp,
            duration_ms=1,
            version=self._components["docling-serve"],
            configuration={"component_versions": self._components},
        )

    def health(self) -> Any: ...

    def versions(self) -> dict[str, str]:
        return {"provider": self._components["docling-serve"], **self._components}


def cache_key_for(components: dict[str, str]) -> str:
    descriptor = ProviderDescriptor.model_validate(
        {"id": "docling", "capabilities": ["document.structure.extract"]}
    )
    executor = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        lambda d: VersionedProvider(d, components),
    )
    result = executor.execute(
        "document.structure.extract",
        b"%PDF-test",
        filename="sample.pdf",
        media_type="application/pdf",
    )
    return result.provenance.cache_key or ""


BASELINE = {"docling-serve": "1.32.0", "docling": "2.124.0"}


def test_identical_component_versions_yield_the_same_key() -> None:
    assert cache_key_for(dict(BASELINE)) == cache_key_for(dict(BASELINE))


def test_upgrading_an_underlying_model_changes_the_key() -> None:
    """docling-serve may stay put while Docling itself changes the output."""
    upgraded = {**BASELINE, "docling": "2.125.0"}

    assert cache_key_for(upgraded) != cache_key_for(dict(BASELINE))


def test_component_versions_are_recorded_in_provenance() -> None:
    descriptor = ProviderDescriptor.model_validate(
        {"id": "docling", "capabilities": ["document.structure.extract"]}
    )
    executor = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        lambda d: VersionedProvider(d, dict(BASELINE)),
    )
    result = executor.execute(
        "document.structure.extract",
        b"%PDF-test",
        filename="sample.pdf",
        media_type="application/pdf",
    )

    assert result.provenance.model_versions == BASELINE
