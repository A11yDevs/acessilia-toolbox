"""Provider descriptors, environment expansion and capability resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from acessilia_toolbox.core.errors import (
    ConfigurationError,
    ProviderNotBoundError,
    ProviderNotFoundError,
)
from acessilia_toolbox.core.provider import (
    REDACTED,
    ProviderDescriptor,
    ProviderRegistry,
    expand_env,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "docling",
        "version": "1.32",
        "endpoint": "http://docling-serve:5001",
        "capabilities": ["document.structure.extract"],
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def test_provider_must_declare_a_capability() -> None:
    with pytest.raises(ValueError):
        descriptor(capabilities=[])


def test_resolve_returns_the_only_provider_for_a_capability() -> None:
    registry = ProviderRegistry([descriptor()])
    assert registry.resolve("document.structure.extract").id == "docling"


def test_resolve_refuses_to_guess_between_providers() -> None:
    """Choosing between interchangeable providers is the agent's decision."""
    registry = ProviderRegistry([descriptor(), descriptor(id="mineru")])
    with pytest.raises(ProviderNotBoundError):
        registry.resolve("document.structure.extract")

    assert registry.resolve("document.structure.extract", "mineru").id == "mineru"


def test_resolve_rejects_a_provider_that_lacks_the_capability() -> None:
    registry = ProviderRegistry([descriptor()])
    with pytest.raises(ProviderNotBoundError):
        registry.resolve("document.ocr", "docling")


def test_resolve_reports_when_no_provider_implements_the_capability() -> None:
    registry = ProviderRegistry([descriptor()])
    with pytest.raises(ProviderNotFoundError):
        registry.resolve("speech.synthesize")


def test_registry_rejects_duplicate_providers() -> None:
    registry = ProviderRegistry([descriptor()])
    with pytest.raises(ConfigurationError):
        registry.register(descriptor())


def test_public_payload_redacts_credentials() -> None:
    entry = descriptor(
        id="minio",
        capabilities=["artifact.store"],
        config={"access_key": "AKIA123", "secret_key": "s3cr3t", "bucket": "acessilia"},
    )
    payload = entry.public_payload()

    assert payload["config"]["access_key"] == REDACTED
    assert payload["config"]["secret_key"] == REDACTED
    assert payload["config"]["bucket"] == "acessilia"
    assert "s3cr3t" not in str(payload)


def test_expand_env_resolves_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCLING_SERVE_URL", "http://localhost:5001")
    assert expand_env("${DOCLING_SERVE_URL}/v1") == "http://localhost:5001/v1"
    assert expand_env({"a": ["${DOCLING_SERVE_URL}"]}) == {"a": ["http://localhost:5001"]}
    assert expand_env(600) == 600


def test_expand_env_fails_loudly_on_unset_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOOLBOX_ABSENT", raising=False)
    with pytest.raises(ConfigurationError):
        expand_env("${TOOLBOX_ABSENT}")


def test_from_mapping_requires_a_providers_list() -> None:
    with pytest.raises(ConfigurationError):
        ProviderRegistry.from_mapping({"provider": []})


def test_from_file_reports_missing_configuration(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        ProviderRegistry.from_file(tmp_path / "absent.yaml")


def test_shipped_provider_topology_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCLING_SERVE_URL", "http://localhost:5001")
    registry = ProviderRegistry.from_file(PROJECT_ROOT / "providers-config.yaml")

    docling = registry.get("docling")
    assert docling.endpoint == "http://localhost:5001"
    assert docling.implements("document.structure.extract")
