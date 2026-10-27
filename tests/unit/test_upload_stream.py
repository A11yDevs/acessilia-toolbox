from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_artifacts_api import client as client_fixture
from tests.integration.test_artifacts_api import store as store_fixture

from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

client = client_fixture
store = store_fixture


class BoundedStream(BytesIO):
    def read(self, size=-1):
        assert 0 < size <= 1024 * 1024
        return super().read(size)


def test_filesystem_stream_is_bounded_and_metadata_immutable(tmp_path):
    storage = FilesystemArtifactStore(tmp_path)
    payload = b"a" * (3 * 1024 * 1024 + 2)
    first = storage.put_stream(BoundedStream(payload), filename="first.pdf")
    second = storage.put_stream(BoundedStream(payload), filename="second.pdf")
    assert first == second
    assert first.artifact_id == fingerprint_bytes(payload)
    assert first.size == len(payload)
    assert storage.get(first.artifact_id) == payload


def test_upload_endpoint_uses_stream(client: TestClient, store, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("byte-based store used")

    monkeypatch.setattr(store, "put", forbid)
    payload = b"x" * (2 * 1024 * 1024 + 5)
    response = client.post("/v1/artifacts", files={"file": ("big.pdf", payload, "application/pdf")})
    assert response.status_code == 200
    assert response.json()["artifact_id"] == fingerprint_bytes(payload)
    assert store.get(response.json()["artifact_id"]) == payload


def test_stream_upload_enforces_limit(client: TestClient, monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.api.rest.MAX_UPLOAD_BYTES", 3)
    assert client.post("/v1/artifacts", files={"file": ("a", b"1234")}).status_code == 413


def test_executor_streams_to_docling_and_preserves_fingerprint(tmp_path):
    from datetime import UTC, datetime

    from tests.unit.test_executor import MANIFEST

    from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
    from acessilia_toolbox.core.executor import CapabilityExecutor
    from acessilia_toolbox.core.normalization.extraction import ExtractionResult
    from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
    from acessilia_toolbox.providers.docling import DoclingProvider

    class StreamingDocling(DoclingProvider):
        def versions(self):
            return {"provider": "test"}

        def execute(self, capability_id, payload, **kwargs):
            assert not isinstance(payload, bytes)
            assert payload.read(8) == b"%PDF-tes"
            timestamp = datetime.now(UTC)
            return ExtractionResult(
                document={"result": "ok"},
                backend="docling",
                started_at=timestamp,
                completed_at=timestamp,
                duration_ms=0,
                version="test",
                configuration={},
            )

    descriptor = ProviderDescriptor(id="docling", capabilities=["document.structure.extract"])
    executor = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        StreamingDocling,
    )
    payload = b"%PDF-test payload"
    result = executor.execute(
        "document.structure.extract",
        BoundedStream(payload),
        filename="test.pdf",
        media_type="application/pdf",
    )
    assert result.provenance.input_fingerprints == [fingerprint_bytes(payload)]


def test_artifact_execution_reads_storage_in_chunks(client, store, monkeypatch):
    payload = b"%PDF-" + b"x" * (2 * 1024 * 1024)
    ref = store.put(payload, filename="file.pdf", media_type="application/pdf")

    def forbidden(*args, **kwargs):
        raise AssertionError("whole-object read was used")

    monkeypatch.setattr(store, "get", forbidden)
    response = client.post(
        "/v1/capabilities/document.structure.extract:execute", data={"artifact_id": ref.artifact_id}
    )
    assert response.status_code == 200, response.text
    assert response.json()["provenance"]["input_fingerprints"] == [ref.artifact_id]


def test_streamed_read_failover_discards_partial_primary_and_preserves_errors(tmp_path):
    from acessilia_toolbox.core.errors import ProviderUnavailableError
    from acessilia_toolbox.providers.storage import FailoverArtifactStore

    fallback = FilesystemArtifactStore(tmp_path)
    ref = fallback.put(b"correct", filename="one.pdf")

    class Offline:
        backend = "s3"

        def copy_to(self, artifact_id, destination, *, max_bytes):
            destination.write(b"partial")
            raise ProviderUnavailableError("offline")

        def stat(self, artifact_id):
            raise ProviderUnavailableError("offline")

    store = FailoverArtifactStore(Offline(), fallback)
    output = BytesIO()
    store.copy_to(ref.artifact_id, output, max_bytes=1024)
    assert output.getvalue() == b"correct"
    with pytest.raises(ProviderUnavailableError):
        store.copy_to(fingerprint_bytes(b"missing"), BytesIO(), max_bytes=1024)
