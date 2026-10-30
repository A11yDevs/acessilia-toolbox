"""rapidocr-orient adapter behavior, exercised over simulated transport."""

from __future__ import annotations

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
from acessilia_toolbox.providers.rapidocr_orient import RapidOcrOrientProvider

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

