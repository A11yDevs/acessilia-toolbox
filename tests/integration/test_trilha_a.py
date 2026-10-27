"""Regressions for release 0.2.0 Trilha A."""


import pytest
from tests.integration.test_artifacts_api import client as client_fixture
from tests.integration.test_artifacts_api import store as store_fixture
from tests.unit.test_artifact_storage import FakeS3Client

from acessilia_toolbox.core.fingerprint import fingerprint_bytes
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


def test_concurrent_filesystem_uploads_keep_one_metadata_record(store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        refs = list(pool.map(lambda i: store.put(b"same", filename=f"upload-{i}.pdf"), range(8)))
    assert all(ref == refs[0] for ref in refs)


