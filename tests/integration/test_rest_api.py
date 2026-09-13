"""REST facade behavior.

Runs against a stubbed provider so the HTTP contract is verified without
Docker; provider conformance itself lives in the contract suite.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
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
    """When TOOLBOX_API_KEY is empty, all endpoints work without a token."""
    response = client.get("/v1/capabilities")
    assert response.status_code == 200
