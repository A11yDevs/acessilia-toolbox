"""Artifact endpoints and execution by reference."""

from __future__ import annotations

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
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

pytestmark = pytest.mark.integration

PDF = "application/pdf"
PAYLOAD = b"%PDF-test"
CAPABILITY = "document.structure.extract"

MANIFEST = {
    "id": CAPABILITY,
    "version": 1,
    "description": "Extract document structure.",
    "input": {"schema": "artifact/document@1", "media_types": [PDF]},
    "output": {"schema": "artifact/structured-document@1"},
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
            duration_ms=4,
            version="1.32.0",
            configuration={},
        )

    def versions(self) -> dict[str, str]:
        return {"provider": "1.32.0"}

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id, healthy=True, checked_at=datetime.now(UTC)
        )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "objects")


@pytest.fixture
def client(store: FilesystemArtifactStore) -> TestClient:
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {"id": "docling", "capabilities": [CAPABILITY]}
            )
        ]
    )
    app = create_app(capabilities, providers, store=store)
    app.state.executor = CapabilityExecutor(
        capabilities, providers, StubProvider, store=store
    )
    return TestClient(app)


def store_artifact(client: TestClient, payload: bytes = PAYLOAD):
    return client.post(
        "/v1/artifacts", files={"file": ("sample.pdf", payload, PDF)}
    )


def test_stored_artifact_returns_a_content_addressable_reference(
    client: TestClient,
) -> None:
    response = store_artifact(client)

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_id"].startswith("sha256:")
    assert body["size"] == len(PAYLOAD)
    assert body["filename"] == "sample.pdf"


def test_storing_the_same_content_twice_returns_the_same_id(client: TestClient) -> None:
    assert store_artifact(client).json()["artifact_id"] == (
        store_artifact(client).json()["artifact_id"]
    )


def test_artifact_round_trips_through_the_api(client: TestClient) -> None:
    artifact_id = store_artifact(client).json()["artifact_id"]

    response = client.get(f"/v1/artifacts/{artifact_id}")

    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["etag"] == artifact_id


def test_artifact_metadata_is_available(client: TestClient) -> None:
    artifact_id = store_artifact(client).json()["artifact_id"]

    body = client.get(f"/v1/artifacts/{artifact_id}/metadata").json()

    assert body["media_type"] == PDF
    assert body["storage_backend"] == "filesystem"


def test_unknown_artifact_returns_a_machine_readable_error(client: TestClient) -> None:
    response = client.get("/v1/artifacts/sha256:" + "0" * 64)

    assert response.status_code == 404
    assert response.json()["code"] == "artifact_not_found"


def test_empty_uploads_are_rejected(client: TestClient) -> None:
    response = client.post("/v1/artifacts", files={"file": ("empty.pdf", b"", PDF)})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_capability_executes_from_a_stored_artifact(client: TestClient) -> None:
    """Large inputs need not be re-transmitted on every call."""
    artifact_id = store_artifact(client).json()["artifact_id"]

    response = client.post(
        f"/v1/capabilities/{CAPABILITY}:execute",
        data={"artifact_id": artifact_id},
    )

    assert response.status_code == 200
    assert response.json()["document"]["source"]["filename"] == "sample.pdf"


def test_execution_requires_an_input(client: TestClient) -> None:
    response = client.post(f"/v1/capabilities/{CAPABILITY}:execute", data={})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_execution_rejects_both_inputs_at_once(client: TestClient) -> None:
    artifact_id = store_artifact(client).json()["artifact_id"]

    response = client.post(
        f"/v1/capabilities/{CAPABILITY}:execute",
        files={"file": ("sample.pdf", PAYLOAD, PDF)},
        data={"artifact_id": artifact_id},
    )

    assert response.status_code == 400


def test_execution_result_is_persisted_as_an_artifact(
    client: TestClient, store: FilesystemArtifactStore
) -> None:
    response = client.post(
        f"/v1/capabilities/{CAPABILITY}:execute",
        files={"file": ("sample.pdf", PAYLOAD, PDF)},
    )

    [artifact] = response.json()["artifacts"]
    assert store.exists(artifact["artifact_id"])
    assert artifact["media_type"] == "application/json"


def test_artifact_endpoints_are_published_in_openapi(client: TestClient) -> None:
    paths = client.get("/v1/openapi.json").json()["paths"]

    assert "/v1/artifacts" in paths
    assert "/v1/artifacts/{artifact_id}" in paths
