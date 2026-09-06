"""Capability execution against a stubbed provider.

Keeps the orchestration path — validation, resolution, normalization,
provenance — testable without Docker or ML models.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.fixtures.documents import FakeDocument

from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderNotBoundError,
    UnsupportedMediaTypeError,
)
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import (
    ProviderDescriptor,
    ProviderHealth,
    ProviderRegistry,
)

PDF = "application/pdf"
PAYLOAD = b"%PDF-test"

MANIFEST = {
    "id": "document.structure.extract",
    "version": 1,
    "description": "Extract document structure.",
    "input": {"schema": "artifact/document@1", "media_types": [PDF]},
    "output": {"schema": "artifact/structured-document@1"},
}


class StubProvider:
    """Records what the executor asked for and returns a canned extraction."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        self.calls.append(
            {
                "capability": capability_id,
                "size": len(payload),
                "filename": filename,
                "media_type": media_type,
                "parameters": dict(parameters or {}),
            }
        )
        timestamp = datetime(2026, 9, 6, tzinfo=UTC)
        return ExtractionResult(
            document=FakeDocument(),
            backend=self.descriptor.id,
            started_at=timestamp,
            completed_at=timestamp,
            duration_ms=42,
            version="1.32-stub",
            configuration={"extractor": "stub"},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id, healthy=True, checked_at=datetime.now(UTC)
        )

    def versions(self) -> dict[str, str]:
        return {"provider": "1.32-stub"}


@pytest.fixture
def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "docling",
            "version": "1.32",
            "endpoint": "http://docling-serve:5001",
            "capabilities": ["document.structure.extract"],
        }
    )


@pytest.fixture
def provider(descriptor: ProviderDescriptor) -> StubProvider:
    return StubProvider(descriptor)


@pytest.fixture
def executor(descriptor: ProviderDescriptor, provider: StubProvider) -> CapabilityExecutor:
    return CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        lambda _: provider,
    )


def run(executor: CapabilityExecutor, **overrides: Any):
    request: dict[str, Any] = {
        "capability_id": "document.structure.extract",
        "payload": PAYLOAD,
        "filename": "sample.pdf",
        "media_type": PDF,
    }
    request.update(overrides)
    capability_id = request.pop("capability_id")
    payload = request.pop("payload")
    return executor.execute(capability_id, payload, **request)


def test_execution_returns_a_normalized_document(executor: CapabilityExecutor) -> None:
    result = run(executor)

    assert result.status == "succeeded"
    assert result.capability == "document.structure.extract@1"
    assert result.provider == "docling"
    assert result.document["$schema"] == "urn:a11y-devs:schema:processing-manifest:1.1.0"
    assert result.document["summary"]["element_count"] == 3


def test_execution_records_provenance(executor: CapabilityExecutor) -> None:
    result = run(executor)
    provenance = result.provenance

    assert provenance.provider_version == "1.32-stub"
    assert provenance.input_fingerprints == [fingerprint_bytes(PAYLOAD)]
    assert provenance.duration_ms == 42
    assert provenance.cache_key
    assert provenance.cache_hit is False


def test_cache_key_is_reproducible_across_executions(executor: CapabilityExecutor) -> None:
    assert run(executor).provenance.cache_key == run(executor).provenance.cache_key


def test_parameters_change_the_cache_key(executor: CapabilityExecutor) -> None:
    plain = run(executor).provenance.cache_key
    with_params = run(executor, parameters={"tables": True}).provenance.cache_key

    assert plain != with_params


def test_provider_receives_the_original_upload(
    executor: CapabilityExecutor, provider: StubProvider
) -> None:
    run(executor, parameters={"tables": True})

    assert provider.calls == [
        {
            "capability": "document.structure.extract",
            "size": len(PAYLOAD),
            "filename": "sample.pdf",
            "media_type": PDF,
            "parameters": {"tables": True},
        }
    ]


def test_rejects_unsupported_media_types(executor: CapabilityExecutor) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        run(executor, media_type="application/zip")


def test_rejects_empty_payloads(executor: CapabilityExecutor) -> None:
    with pytest.raises(InvalidInputError):
        run(executor, payload=b"")


@pytest.mark.parametrize(
    "filename",
    ["../../etc/passwd", "sub/dir/sample.pdf", "..", "", "/absolute.pdf"],
)
def test_rejects_filenames_that_escape_the_staging_directory(
    executor: CapabilityExecutor, filename: str
) -> None:
    """Uploads are untrusted; the name must never steer a write off the staging dir."""
    with pytest.raises(InvalidInputError):
        run(executor, filename=filename)


def test_rejects_a_provider_that_lacks_the_capability(
    descriptor: ProviderDescriptor, provider: StubProvider
) -> None:
    other = ProviderDescriptor.model_validate(
        {"id": "mineru", "capabilities": ["document.ocr"]}
    )
    executor = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor, other]),
        lambda _: provider,
    )

    with pytest.raises(ProviderNotBoundError):
        run(executor, provider_id="mineru")


def test_language_reaches_the_normalized_document(executor: CapabilityExecutor) -> None:
    assert run(executor, language="en-US").document["language"] == "en-US"
