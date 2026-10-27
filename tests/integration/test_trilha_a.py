"""Regressions for release 0.2.0 Trilha A."""

from datetime import UTC, datetime
from threading import BoundedSemaphore

import pytest
import yaml
from tests.integration.test_artifacts_api import client as client_fixture
from tests.integration.test_artifacts_api import store as store_fixture
from tests.unit.test_artifact_storage import FakeS3Client

from acessilia_toolbox.api.schemas import ReadinessResponse
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderHealth
from acessilia_toolbox.providers.storage import S3ArtifactStore

client = client_fixture
store = store_fixture


def test_metadata_does_not_load_payload(client, store, monkeypatch):
    ref = store.put(b"large artifact", filename="document.pdf")

    def forbidden(*args):
        pytest.fail("metadata must never read bytes")

    monkeypatch.setattr(store, "get", forbidden)
    assert client.get(f"/v1/artifacts/{ref.artifact_id}/metadata").status_code == 200
    assert client.get(f"/v1/artifacts/{fingerprint_bytes(b'missing')}/metadata").status_code == 404


@pytest.mark.parametrize("backend", ["filesystem", "s3"])
def test_first_upload_metadata_is_immutable(store, backend):
    if backend == "s3":

        class ConditionalS3(FakeS3Client):
            def put_object(self, **kwargs):
                if kwargs.get("IfNoneMatch") == "*" and kwargs["Key"] in self.objects:
                    error = Exception("already exists")
                    error.response = {"Error": {"Code": "PreconditionFailed"}}
                    raise error
                return super().put_object(**kwargs)

        store = object.__new__(S3ArtifactStore)
        store.bucket = "test"
        store._client = ConditionalS3()
    first = store.put(b"same", filename="first.pdf", media_type="application/pdf")
    second = store.put(b"same", filename="other.bin", media_type="application/octet-stream")
    assert second == first
    assert store.stat(first.artifact_id) == first


@pytest.mark.parametrize("healthy", [True, False])
def test_readiness_checks_provider_without_extraction(client, monkeypatch, healthy):
    class Adapter:
        def health(self):
            return ProviderHealth(provider="docling", healthy=healthy, checked_at=datetime.now(UTC))

    monkeypatch.setattr("acessilia_toolbox.api.rest.create_adapter", lambda _: Adapter())
    assert client.get("/v1/health").status_code == 200
    response = client.get("/v1/ready")
    assert response.status_code == (200 if healthy else 503)
    ReadinessResponse.model_validate(response.json())
    responses = client.get("/v1/openapi.json").json()["paths"]["/v1/ready"]["get"]["responses"]
    schema = responses["200"]["content"]["application/json"]["schema"]
    assert schema == responses["503"]["content"]["application/json"]["schema"]
    assert schema["$ref"] == "#/components/schemas/ReadinessResponse"



@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.staging.yml"])
def test_bucket_init_precedes_api_start(filename):
    from pathlib import Path

    config = yaml.safe_load(Path(filename).read_text())
    assert (
        config["services"]["toolbox"]["depends_on"]["minio-init"]["condition"]
        == "service_completed_successfully"
    )


def test_uploads_share_execution_memory_budget(client):
    client.app.state.execution_slots = BoundedSemaphore(1)
    client.app.state.execution_slots.acquire()
    response = client.post(
        "/v1/artifacts", files={"file": ("large.pdf", b"payload", "application/pdf")}
    )
    assert response.status_code == 503


def test_concurrent_filesystem_uploads_keep_one_metadata_record(store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        refs = list(pool.map(lambda i: store.put(b"same", filename=f"upload-{i}.pdf"), range(8)))
    assert all(ref == refs[0] for ref in refs)


def test_oversized_upload_is_413_and_releases_slot(client, monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.api.rest.MAX_UPLOAD_BYTES", 3)
    client.app.state.execution_slots = BoundedSemaphore(1)
    response = client.post(
        "/v1/artifacts", files={"file": ("large.pdf", b"1234", "application/pdf")}
    )
    assert response.status_code == 413
    assert client.app.state.execution_slots.acquire(blocking=False)


def test_cancelled_upload_releases_memory_budget(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from acessilia_toolbox.api import rest

    slots = BoundedSemaphore(1)

    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(rest, "_read_upload", cancelled)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(execution_slots=slots)))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rest.store_artifact(None, None, request))
    assert slots.acquire(blocking=False)
