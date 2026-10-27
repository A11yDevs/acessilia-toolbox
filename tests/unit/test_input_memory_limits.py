from datetime import UTC, datetime
from io import BytesIO

import pytest
from tests.unit.test_executor import MANIFEST

from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import ConfigurationError, UploadTooLargeError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.input_memory import read_in_memory
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry


class Adapter:
    supports_streaming_input = False

    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.seen = None

    def versions(self):
        return {"provider": "test"}

    def execute(self, capability_id, payload, **kwargs):
        self.seen = payload
        now = datetime.now(UTC)
        return ExtractionResult(
            document={"ok": True},
            backend="fake",
            started_at=now,
            completed_at=now,
            duration_ms=0,
            version="test",
            configuration={},
        )


def executor_for(adapter):
    descriptor = ProviderDescriptor(id="fake", capabilities=["document.structure.extract"])
    instance = adapter(descriptor)
    runner = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        lambda _: instance,
    )
    return runner, instance


def test_legacy_provider_rejects_large_stream_without_reading_whole_file(monkeypatch):
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", "8")
    runner, adapter = executor_for(Adapter)
    with pytest.raises(UploadTooLargeError):
        runner.execute(
            "document.structure.extract",
            BytesIO(b"%PDF-12345678"),
            filename="test.pdf",
            media_type="application/pdf",
        )
    assert adapter.seen is None


def test_legacy_provider_keeps_small_input_compatible(monkeypatch):
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", "8")
    runner, adapter = executor_for(Adapter)
    runner.execute(
        "document.structure.extract",
        BytesIO(b"%PDF-1"),
        filename="test.pdf",
        media_type="application/pdf",
    )
    assert adapter.seen == b"%PDF-1"


def test_streaming_adapter_is_not_limited_by_legacy_budget(monkeypatch):
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", "8")

    class StreamingAdapter(Adapter):
        supports_streaming_input = True

    runner, adapter = executor_for(StreamingAdapter)
    payload = BytesIO(b"%PDF-" + b"a" * 20)
    runner.execute(
        "document.structure.extract", payload, filename="test.pdf", media_type="application/pdf"
    )
    assert adapter.seen is payload


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_invalid_memory_limit_is_configuration_error(monkeypatch, value):
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", value)
    with pytest.raises(ConfigurationError):
        read_in_memory(BytesIO(b"data"))


def test_unknown_stream_length_still_stops_at_limit(monkeypatch):
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", "4")

    class BoundedRead(BytesIO):
        def read(self, size=-1):
            assert size == 5
            return super().read(size)

    with pytest.raises(UploadTooLargeError):
        read_in_memory(BoundedRead(b"abcdef"))


def test_image_auto_rotate_is_bounded_even_for_stream_provider(monkeypatch):
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", "8")

    class StreamingAdapter(Adapter):
        supports_streaming_input = True

    descriptor = ProviderDescriptor(id="fake", capabilities=["document.structure.extract"])
    instance = StreamingAdapter(descriptor)
    manifest = {
        **MANIFEST,
        "input": {"schema": "artifact/document@1", "media_types": ["image/png"]},
    }
    runner = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(manifest)]),
        ProviderRegistry([descriptor]),
        lambda _: instance,
    )
    with pytest.raises(UploadTooLargeError):
        runner.execute(
            "document.structure.extract",
            BytesIO(b"x" * 9),
            filename="x.png",
            media_type="image/png",
            parameters={"auto_rotate": True},
        )
    assert instance.seen is None


def test_http_docling_ocr_accepts_a_file_stream():
    from tests.unit.test_docling_ocr_provider import convert_handler, provider_with

    adapter = provider_with(convert_handler)
    result = adapter.execute(
        "document.ocr", BytesIO(b"fake-image-data"), filename="scan.png", media_type="image/png"
    )
    assert adapter.supports_streaming_input
    assert result.document["item_count"] == 2


def test_http_docling_layout_accepts_a_file_stream():
    from tests.unit.test_docling_layout_provider import convert_handler, provider_with

    adapter = provider_with(convert_handler)
    result = adapter.execute(
        "document.layout.analyze",
        BytesIO(b"%PDF-test"),
        filename="sample.pdf",
        media_type="application/pdf",
    )
    assert adapter.supports_streaming_input
    assert result.document["region_count"] > 0


def test_legacy_store_upload_is_bounded(monkeypatch):
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", "4")

    class LegacyStore:
        backend = "legacy"

        def put(self, payload, **kwargs):
            raise AssertionError("should reject before legacy write")

    runner, _ = executor_for(Adapter)
    runner._store = LegacyStore()
    with pytest.raises(UploadTooLargeError):
        runner.store_artifact_stream(BytesIO(b"12345"), media_type="text/plain", filename="x.txt")


def test_http_returns_413_when_provider_requires_larger_in_memory_input(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from tests.integration.test_artifacts_api import (
        CAPABILITY,
        StubProvider,
    )
    from tests.integration.test_artifacts_api import (
        MANIFEST as API_MANIFEST,
    )

    from acessilia_toolbox.api.app import create_app
    from acessilia_toolbox.providers.storage import FilesystemArtifactStore

    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", "8")
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(API_MANIFEST)])
    providers = ProviderRegistry([ProviderDescriptor(id="docling", capabilities=[CAPABILITY])])
    store = FilesystemArtifactStore(tmp_path)
    app = create_app(capabilities, providers, store=store)
    app.state.executor = CapabilityExecutor(capabilities, providers, StubProvider, store=store)
    response = TestClient(app).post(
        "/v1/capabilities/document.structure.extract:execute",
        files={"file": ("x.pdf", b"%PDF-123456789", "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "upload_too_large"
