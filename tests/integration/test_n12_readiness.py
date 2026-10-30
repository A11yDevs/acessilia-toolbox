"""S3 readiness exercises write authorization without growing the bucket."""

from datetime import UTC, datetime

import pytest
from tests.integration.test_artifacts_api import client as client_fixture
from tests.integration.test_artifacts_api import store as store_fixture
from tests.unit.test_artifact_storage import FakeS3Client

from acessilia_toolbox.core.provider import ProviderHealth
from acessilia_toolbox.providers import storage
from acessilia_toolbox.providers.storage import (
    FailoverArtifactStore,
    S3ArtifactStore,
    storage_ready,
)

client = client_fixture
store = store_fixture


class PermissionS3(FakeS3Client):
    writable = True

    def head_bucket(self, **kwargs):
        return {}

    def put_object(self, **kwargs):
        if not self.writable:
            error = Exception("read-only policy")
            error.response = {"Error": {"Code": "AccessDenied"}}
            raise error
        if kwargs.get("IfNoneMatch") == "*" and kwargs["Key"] in self.objects:
            error = Exception("already exists")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        return super().put_object(**kwargs)


def primary():
    store = object.__new__(S3ArtifactStore)
    store.bucket = "fixture"
    store._client = PermissionS3()
    return store


@pytest.mark.parametrize("writable", [True, False])
def test_readiness_matches_s3_upload_permissions(client, monkeypatch, writable):
    store = primary()
    store._client.writable = writable
    client.app.state.store = store
    client.app.state.executor._store = store

    class Healthy:
        def health(self):
            return ProviderHealth(provider="docling", healthy=True, checked_at=datetime.now(UTC))

    monkeypatch.setattr("acessilia_toolbox.api.rest.create_adapter", lambda _: Healthy())
    ready = client.get("/v1/ready")
    upload = client.post(
        "/v1/artifacts", files={"file": ("doc.pdf", b"%PDF-fixture", "application/pdf")}
    )
    assert ready.status_code == (200 if writable else 503)
    assert ready.json()["checks"]["storage"] is writable
    assert upload.status_code == (200 if writable else 503)


def test_s3_probe_does_not_accumulate_objects_or_change_existing_metadata():
    store = primary()
    for _ in range(3):
        assert storage_ready(store)
    assert len(store._client.objects) == 1
    before = dict(store._client.objects)
    assert storage_ready(store)
    assert store._client.objects == before


def test_s3_probe_caches_results_then_rechecks_permissions(monkeypatch, caplog):
    store = primary()
    clock = [100.0]
    monkeypatch.setattr(storage.time, "monotonic", lambda: clock[0])
    assert storage_ready(store)
    store._client.writable = False
    clock[0] += 4.9
    assert storage_ready(store)
    clock[0] += 0.1
    assert not storage_ready(store)
    assert "storage readiness probe failed" in caplog.text
    assert "read-only policy" in caplog.text
    store._client.writable = True
    assert not storage_ready(store)
    clock[0] += 5.0
    assert storage_ready(store)


def test_readiness_keeps_existing_fallback_policy(store):
    s3 = primary()
    s3._client.writable = False
    assert storage_ready(FailoverArtifactStore(s3, store))
