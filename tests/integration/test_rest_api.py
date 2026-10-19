"""REST facade behavior.

Runs against a stubbed provider so the HTTP contract is verified without
Docker; provider conformance itself lives in the contract suite.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.documents import FakeDocument

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import (
    ProviderDescriptor,
    ProviderHealth,
    ProviderRegistry,
)

pytestmark = pytest.mark.integration

PDF = "application/pdf"
CAPABILITY = "document.structure.extract"

MANIFEST = {
    "id": CAPABILITY,
    "version": 1,
    "description": "Extract document structure.",
    "input": {"schema": "artifact/document@1", "media_types": [PDF]},
    "output": {"schema": "artifact/structured-document@1"},
    "semantics": {"requires": ["document_available"], "produces": ["structured"]},
    "providers": [{"id": "docling"}],
}


class StubProvider:
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
        timestamp = datetime(2026, 9, 6, tzinfo=UTC)
        return ExtractionResult(
            document=FakeDocument(),
            backend="docling",
            started_at=timestamp,
            completed_at=timestamp,
            duration_ms=7,
            version="1.32.0",
            configuration={"component_versions": {"docling": "2.124.0"}},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            version="1.32.0",
            checked_at=datetime.now(UTC),
        )

    def versions(self) -> dict[str, str]:
        return {"provider": "1.32.0", "docling": "2.124.0"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "acessilia_toolbox.api.rest.create_adapter", lambda d: StubProvider(d)
    )
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {
                    "id": "docling",
                    "version": "1.32",
                    "endpoint": "http://docling-serve:5001",
                    "capabilities": [CAPABILITY],
                    "config": {"access_key": "SUPERSECRET"},
                }
            )
        ]
    )
    app = create_app(capabilities, providers)
    app.state.executor = CapabilityExecutor(
        capabilities, providers, lambda d: StubProvider(d)
    )
    return TestClient(app)


def upload(client: TestClient, **data: Any):
    return client.post(
        f"/v1/capabilities/{CAPABILITY}:execute",
        files={"file": ("sample.pdf", b"%PDF-test", PDF)},
        data=data,
    )


def test_health_reports_the_service_version(client: TestClient) -> None:
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["version"]


def test_health_reports_the_build_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image passes GIT_COMMIT/IMAGE_TAG; health must surface them.

    Without them there is no way to tell which revision a running Toolbox is
    serving, which is what made the staging/core comparison a guess.
    """
    monkeypatch.setenv("GIT_COMMIT", "abcdef1234567890")
    monkeypatch.setenv("IMAGE_TAG", "develop")

    body = client.get("/v1/health").json()

    assert body["git_commit"] == "abcdef1234567890"
    assert body["image_tag"] == "develop"


def test_health_build_identity_defaults_to_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("IMAGE_TAG", raising=False)

    body = client.get("/v1/health").json()

    assert body["git_commit"] == ""
    assert body["image_tag"] == ""


def test_capabilities_are_listed(client: TestClient) -> None:
    response = client.get("/v1/capabilities")

    assert response.status_code == 200
    [capability] = response.json()
    assert capability["id"] == CAPABILITY
    assert capability["deterministic"] is True
    assert capability["providers"] == ["docling"]


def test_capability_detail_exposes_planning_semantics(client: TestClient) -> None:
    response = client.get(f"/v1/capabilities/{CAPABILITY}")

    assert response.status_code == 200
    body = response.json()
    assert body["requires"] == ["document_available"]
    assert body["produces"] == ["structured"]
    assert body["output_schema"] == "artifact/structured-document@1"


def test_unknown_capability_returns_a_machine_readable_error(client: TestClient) -> None:
    response = client.get("/v1/capabilities/speech.synthesize")

    assert response.status_code == 404
    assert response.json()["code"] == "capability_not_found"


def test_execution_returns_document_and_provenance(client: TestClient) -> None:
    response = upload(client)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["capability"] == f"{CAPABILITY}@1"
    assert body["provider"] == "docling"
    assert body["document"]["$schema"] == "urn:a11y-devs:schema:processing-manifest:1.1.0"
    assert body["provenance"]["provider_version"] == "1.32.0"
    assert body["provenance"]["model_versions"] == {"docling": "2.124.0"}


def test_execution_reports_an_artifact_reference(client: TestClient) -> None:
    artifacts = upload(client).json()["artifacts"]

    assert len(artifacts) == 1
    assert artifacts[0]["artifact_id"].startswith("sha256:")
    assert artifacts[0]["media_type"] == "application/json"
    assert artifacts[0]["size"] > 0


def test_execution_accepts_language_and_parameters(client: TestClient) -> None:
    response = upload(client, language="en-US", parameters='{"tables": true}')

    assert response.status_code == 200
    assert response.json()["document"]["language"] == "en-US"


def test_malformed_parameters_are_rejected(client: TestClient) -> None:
    response = upload(client, parameters="not json")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_unsupported_media_type_is_rejected(client: TestClient) -> None:
    response = client.post(
        f"/v1/capabilities/{CAPABILITY}:execute",
        files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")},
    )

    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"


def test_providers_are_listed_without_credentials(client: TestClient) -> None:
    response = client.get("/v1/providers")

    assert response.status_code == 200
    assert "SUPERSECRET" not in response.text
    assert response.json()[0]["config"]["access_key"] == "***"


def test_provider_detail_hides_credentials(client: TestClient) -> None:
    response = client.get("/v1/providers/docling")

    assert response.status_code == 200
    assert "SUPERSECRET" not in response.text


def test_unknown_provider_returns_an_error(client: TestClient) -> None:
    response = client.get("/v1/providers/mineru")

    assert response.status_code == 404
    assert response.json()["code"] == "provider_not_found"


def test_provider_health_is_probed(client: TestClient) -> None:
    response = client.get("/v1/providers/docling/health")

    assert response.status_code == 200
    assert response.json()["healthy"] is True


def test_openapi_document_is_published(client: TestClient) -> None:
    response = client.get("/v1/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/capabilities/{capability_id}:execute" in paths
    assert "/v1/providers/{provider_id}/health" in paths


COMMITTED_OPENAPI = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_committed_openapi_document_matches_the_app() -> None:
    """docs/openapi.json is what the application publishes, so it cannot drift.

    Regenerate it with: UPDATE_OPENAPI=1 pytest -k committed_openapi
    """
    generated = (
        json.dumps(
            create_app(CapabilityRegistry([]), ProviderRegistry([])).openapi(),
            indent=4,
            ensure_ascii=False,
        )
        + "\n"
    )
    if os.getenv("UPDATE_OPENAPI") == "1":
        COMMITTED_OPENAPI.write_text(generated, encoding="utf-8")
    assert COMMITTED_OPENAPI.read_text(encoding="utf-8") == generated, (
        "docs/openapi.json is stale; regenerate it with "
        "UPDATE_OPENAPI=1 pytest -k committed_openapi"
    )


# ── Authentication tests ──────────────────────────────────────────


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with TOOLBOX_API_KEY set to a known value."""
    monkeypatch.setenv("TOOLBOX_API_KEY", "test-key-123")
    # Re-import auth module to pick up the new env var.
    monkeypatch.setattr(
        "acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "test-key-123"
    )
    monkeypatch.setattr(
        "acessilia_toolbox.api.rest.create_adapter", lambda d: StubProvider(d)
    )
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {
                    "id": "docling",
                    "version": "1.32",
                    "endpoint": "http://docling-serve:5001",
                    "capabilities": [CAPABILITY],
                    "config": {"access_key": "SUPERSECRET"},
                }
            )
        ]
    )
    app = create_app(capabilities, providers)
    app.state.executor = CapabilityExecutor(
        capabilities, providers, lambda d: StubProvider(d)
    )
    return TestClient(app)


def test_health_is_public_even_with_auth(auth_client: TestClient) -> None:
    """GET /v1/health must never require authentication."""
    response = auth_client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_endpoints_reject_requests_without_token(auth_client: TestClient) -> None:
    """When auth is active, requests without Authorization header fail."""
    response = auth_client.get("/v1/capabilities")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid token"


def test_endpoints_reject_invalid_token(auth_client: TestClient) -> None:
    """A token that does not match TOOLBOX_API_KEY is rejected."""
    response = auth_client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "authorization_failed"


def test_valid_token_grants_access(auth_client: TestClient) -> None:
    """A request with the correct Bearer token succeeds."""
    response = auth_client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-key-123"},
    )
    assert response.status_code == 200


def test_auth_is_disabled_when_key_is_empty(client: TestClient) -> None:
    """With no key and TOOLBOX_ALLOW_UNAUTHENTICATED=1 (tests/conftest.py), all
    endpoints work without a token."""
    response = client.get("/v1/capabilities")
    assert response.status_code == 200


# ── Fail-closed start-up and catalogue providers ─────────────────


def _bare_registries() -> tuple[CapabilityRegistry, ProviderRegistry]:
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {"id": "docling", "endpoint": "http://docling", "capabilities": [CAPABILITY]}
            )
        ]
    )
    return capabilities, providers


def test_app_refuses_to_start_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forgotten TOOLBOX_API_KEY must not leave every endpoint open."""
    from acessilia_toolbox.core.errors import ConfigurationError

    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    monkeypatch.delenv("TOOLBOX_ALLOW_UNAUTHENTICATED", raising=False)
    with pytest.raises(ConfigurationError, match="TOOLBOX_API_KEY is not set"):
        create_app(*_bare_registries())


@pytest.mark.parametrize("flag", ["0", "", "false", "no"])
def test_only_an_explicit_flag_allows_running_without_a_key(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    from acessilia_toolbox.core.errors import ConfigurationError

    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", flag)
    with pytest.raises(ConfigurationError):
        create_app(*_bare_registries())


def test_explicit_flag_starts_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    client = TestClient(create_app(*_bare_registries()))
    assert client.get("/v1/capabilities").status_code == 200


def test_a_key_needs_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "k")
    monkeypatch.delenv("TOOLBOX_ALLOW_UNAUTHENTICATED", raising=False)
    client = TestClient(create_app(*_bare_registries()))
    assert client.get("/v1/capabilities").status_code == 401
    assert client.get("/v1/capabilities", headers={"Authorization": "Bearer k"}).status_code == 200


@pytest.mark.parametrize("token", ["test-key-12", "test-key-1234", "TEST-KEY-123", ""])
def test_near_miss_tokens_are_rejected(auth_client: TestClient, token: str) -> None:
    """Prefixes, extensions and case changes of the key are all rejected."""
    response = auth_client.get("/v1/capabilities", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_catalogue_lists_registered_providers_and_keeps_the_declared_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider the manifest binds but this deployment does not register is not
    executable, so it is not advertised as a provider (only as declared)."""
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    manifest = {**MANIFEST, "providers": [{"id": "docling"}, {"id": "teleocr"}]}
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(manifest)])
    _, providers = _bare_registries()
    client = TestClient(create_app(capabilities, providers))

    [summary] = client.get("/v1/capabilities").json()
    assert summary["providers"] == ["docling"]
    assert summary["declared_providers"] == ["docling", "teleocr"]
    detail = client.get(f"/v1/capabilities/{CAPABILITY}").json()
    assert detail["providers"] == ["docling"]
    assert detail["declared_providers"] == ["docling", "teleocr"]


def test_catalogue_lists_a_registered_provider_the_manifest_does_not_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`providers` is what a client can execute. The executor resolves any
    registered provider that implements the capability, bound or not, so such a
    provider is listed even though it is not declared."""
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    capabilities, _ = _bare_registries()
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {"id": pid, "endpoint": f"http://{pid}", "capabilities": [CAPABILITY]}
            )
            for pid in ("docling", "mineru")
        ]
    )
    client = TestClient(create_app(capabilities, providers))

    [summary] = client.get("/v1/capabilities").json()
    assert summary["providers"] == ["docling", "mineru"]
    assert summary["declared_providers"] == ["docling"]


def test_catalogue_flags_the_latest_version_and_planning_lists_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every version is published; the one a request without a version runs is
    flagged, and the PDDL domain holds a single action per capability."""
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    capabilities = CapabilityRegistry(
        [CapabilityManifest.model_validate(m) for m in (MANIFEST, {**MANIFEST, "version": 2})]
    )
    _, providers = _bare_registries()
    client = TestClient(create_app(capabilities, providers))

    summaries = client.get("/v1/capabilities").json()
    assert [(s["version"], s["latest"]) for s in summaries] == [(1, False), (2, True)]
    assert client.get(f"/v1/capabilities/{CAPABILITY}").json()["latest"] is True
    assert client.get(f"/v1/capabilities/{CAPABILITY}?version=1").json()["latest"] is False
    domain = client.get("/v1/planning/domain").text
    assert domain.count("(:action document-structure-extract") == 1
