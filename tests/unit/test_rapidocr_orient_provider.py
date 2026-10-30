"""rapidocr-orient adapter behavior, exercised over simulated transport."""

from __future__ import annotations

import threading
from collections.abc import Callable

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers import rapidocr_orient as orient_module
from acessilia_toolbox.providers.rapidocr_orient import RapidOcrOrientProvider


@pytest.fixture(autouse=True)
def _isolated_version_cache():
    with orient_module._VERSION_LOCK:
        orient_module._VERSION_CACHE.clear()
        orient_module._VERSION_REFRESHING.clear()
    yield
    with orient_module._VERSION_LOCK:
        orient_module._VERSION_CACHE.clear()
        orient_module._VERSION_REFRESHING.clear()


def test_version_probe_is_shared_across_adapters_and_execute(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/version":
            return httpx.Response(200, json={"engine_version": "1.3.0"})
        return httpx.Response(200, json=SAMPLE_UPRIGHT_RESPONSE)

    first = provider_with(handler)
    assert first.versions()["provider"] == "1.3.0"
    second = provider_with(handler)
    assert second.versions()["provider"] == "1.3.0"
    second.execute("image.page.orient", b"image", filename="page.png", media_type="image/png")
    assert calls == ["/version", "/orient"]


def test_failed_version_refresh_retains_identity_then_recovers(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(orient_module, "monotonic", lambda: clock[0])
    state = {"fail": False, "version": "1.3.0"}
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if state["fail"]:
            raise httpx.ReadTimeout("version endpoint unavailable")
        return httpx.Response(200, json={"engine_version": state["version"]})

    adapter = provider_with(handler)
    original = adapter.versions()
    clock[0] += orient_module.VERSION_TTL_SECONDS + 1
    state["fail"] = True
    assert adapter.versions() == original
    assert adapter.versions() == original
    assert calls == ["/version", "/version"]
    clock[0] += orient_module.VERSION_RETRY_SECONDS + 1
    state.update(fail=False, version="1.4.0")
    assert adapter.versions()["provider"] == "1.4.0"


def test_slow_version_refresh_serves_last_good_identity_to_other_requests(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(orient_module, "monotonic", lambda: clock[0])
    probing = threading.Event()
    release = threading.Event()
    state = {"slow": False}

    def handler(request):
        if state["slow"]:
            probing.set()
            assert release.wait(5)
        return httpx.Response(200, json={"engine_version": "1.3.0"})

    adapter = provider_with(handler)
    original = adapter.versions()
    clock[0] += orient_module.VERSION_TTL_SECONDS + 1
    state["slow"] = True
    refresher = threading.Thread(target=adapter.versions)
    refresher.start()
    try:
        assert probing.wait(5)
        # The probe is in flight: another request must not wait for it.
        served = {}
        reader = threading.Thread(
            target=lambda: served.update(versions=provider_with(handler).versions())
        )
        reader.start()
        reader.join(2)
        assert not reader.is_alive()
        assert served["versions"] == original
    finally:
        release.set()
        refresher.join(5)
    assert not refresher.is_alive()


def test_version_response_without_fields_keeps_the_full_ttl(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(orient_module, "monotonic", lambda: clock[0])
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    adapter = provider_with(handler)
    assert adapter.versions() == {"provider": adapter.descriptor.version}
    clock[0] += orient_module.VERSION_RETRY_SECONDS + 1
    adapter.versions()
    assert calls == ["/version"]
    clock[0] += orient_module.VERSION_TTL_SECONDS
    adapter.versions()
    assert calls == ["/version", "/version"]


@pytest.mark.parametrize("change", ["endpoint", "version", "config"])
def test_version_cache_separates_deployments_and_configuration(change):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"engine_version": str(len(calls))})

    first = provider_with(handler)
    assert first.versions()["provider"] == "1"
    second = provider_with(handler)
    if change == "endpoint":
        second.base_url = "http://another-orient.test"
    elif change == "version":
        second.descriptor.version = "2.0"
    else:
        second.descriptor.config["model"] = "new"
    assert second.versions()["provider"] == "2"
    assert len(calls) == 2

SAMPLE_UPRIGHT_RESPONSE = {
    "angle": 0,
    "confidence": 0.95,
    "stage": "stage1_upright",
    "original_width": 800,
    "original_height": 1200,
    "oriented_width": 800,
    "oriented_height": 1200,
    "auto_rotated": False,
}

SAMPLE_ROTATED_RESPONSE = {
    "angle": 90,
    "confidence": 0.91,
    "stage": "stage2_evaluated",
    "original_width": 1200,
    "original_height": 800,
    "oriented_width": 800,
    "oriented_height": 1200,
    "auto_rotated": True,
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "rapidocr-orient",
        "version": "1.0",
        "endpoint": "http://rapidocr-orient:5006",
        "capabilities": ["image.page.orient"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler: Callable[[httpx.Request], httpx.Response]) -> RapidOcrOrientProvider:
    adapter = RapidOcrOrientProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def test_factory_creates_adapter() -> None:
    adapter = create_adapter(descriptor())
    assert isinstance(adapter, RapidOcrOrientProvider)
    assert adapter.descriptor.id == "rapidocr-orient"


def test_health_check_healthy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "engine_ready": True, "version": "1.0.0"})

    adapter = provider_with(handler)
    health = adapter.health()
    assert health.healthy is True
    assert health.provider == "rapidocr-orient"
    assert health.version == "1.0.0"


def test_health_check_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    adapter = provider_with(handler)
    health = adapter.health()
    assert health.healthy is False
    assert "ConnectError" in (health.detail or "")


def test_versions_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"version": "1.0.0", "engine_version": "1.3.0", "capability": "image.page.orient"},
        )

    adapter = provider_with(handler)
    versions = adapter.versions()
    assert versions["provider"] == "1.3.0"
    assert versions["engine_version"] == "1.3.0"


def test_versions_exclude_non_version_keys() -> None:
    """Only artifact identity belongs in provenance and the cache key."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "version": "1.0.0",
                "engine": "rapidocr_onnxruntime",
                "engine_version": "1.3.0",
                "capability": "image.page.orient",
            },
        )

    adapter = provider_with(handler)
    versions = adapter.versions()
    assert versions == {"provider": "1.3.0", "version": "1.0.0", "engine_version": "1.3.0"}


def test_versions_fall_back_to_declared_version_when_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    adapter = provider_with(handler)
    assert adapter.versions() == {"provider": "1.0"}


def test_orient_non_object_json_fails_with_accurate_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    adapter = provider_with(handler)
    with pytest.raises(ProviderExecutionError, match="expected a JSON object"):
        adapter.execute(
            "image.page.orient",
            b"fake-image",
            filename="page.png",
            media_type="image/png",
        )


def test_orient_connection_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset")

    adapter = provider_with(handler)
    with pytest.raises(ProviderUnavailableError):
        adapter.execute(
            "image.page.orient",
            b"fake-image",
            filename="page.png",
            media_type="image/png",
        )


def test_orient_upright_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "1.0.0", "engine_version": "1.3.0"})
        return httpx.Response(200, json=SAMPLE_UPRIGHT_RESPONSE)

    adapter = provider_with(handler)
    result = adapter.execute(
        "image.page.orient",
        b"fake-image-bytes",
        filename="page.png",
        media_type="image/png",
    )

    assert result.backend == "rapidocr-orient"
    assert result.document["angle"] == 0
    assert result.document["auto_rotated"] is False
    assert result.document["stage"] == "stage1_upright"
    assert result.configuration["angle"] == 0


def test_orient_rotated_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "1.0.0", "engine_version": "1.3.0"})
        return httpx.Response(200, json=SAMPLE_ROTATED_RESPONSE)

    adapter = provider_with(handler)
    result = adapter.execute(
        "image.page.orient",
        b"fake-image-bytes",
        filename="rotated.png",
        media_type="image/png",
    )

    assert result.backend == "rapidocr-orient"
    assert result.document["angle"] == 90
    assert result.document["auto_rotated"] is True
    assert result.document["stage"] == "stage2_evaluated"


def test_orient_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Timeout")

    adapter = provider_with(handler)
    with pytest.raises(ProviderTimeoutError):
        adapter.execute(
            "image.page.orient",
            b"fake-image",
            filename="page.png",
            media_type="image/png",
        )


def test_orient_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    adapter = provider_with(handler)
    with pytest.raises(ProviderExecutionError) as exc_info:
        adapter.execute(
            "image.page.orient",
            b"fake-image",
            filename="page.png",
            media_type="image/png",
        )
    assert exc_info.value.details.get("status_code") == 500
    assert "rapidocr-orient failed: HTTP 500 - Internal Server Error" in str(exc_info.value)

