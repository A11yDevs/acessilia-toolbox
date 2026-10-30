"""The Toolbox in VLM gateway mode, and the env-driven VLM provider settings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from acessilia_toolbox.api.gateway_app import create_gateway_app
from acessilia_toolbox.core.errors import ConfigurationError
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import openai_vlm
from acessilia_toolbox.providers.openai_vlm import OpenAiCompatibleVlmProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KEY = "gateway-key"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", KEY)
    openai_vlm._SEMAPHORES.clear()
    yield
    openai_vlm._SEMAPHORES.clear()


def registry() -> ProviderRegistry:
    return ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {
                    "id": "qwen3-vl-8b",
                    "endpoint": "http://127.0.0.1:8080",
                    "capabilities": list(openai_vlm.CAPABILITIES),
                    "config": {"model": "qwen3-vl-8b"},
                }
            )
        ]
    )


async def call(app, method: str, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://gateway"
    ) as client:
        return await client.request(method, path, headers={"Authorization": f"Bearer {KEY}"})


def test_gateway_mode_serves_only_health_and_the_vlm_routes() -> None:
    app = create_gateway_app(registry())

    paths = set(app.openapi()["paths"])

    assert paths == {
        "/v1/health",
        "/v1/vlm/chat/completions",
        "/v1/vlm/v1/chat/completions",
        "/v1/vlm/health",
    }
    assert asyncio.run(call(app, "GET", "/v1/health")).status_code == 200
    assert asyncio.run(call(app, "GET", "/v1/capabilities")).status_code == 404
    # Only the three routes the deployment lists: no API docs, and no
    # readiness route, which checks extraction and storage a gateway lacks.
    for path in ("/v1/docs", "/v1/openapi.json", "/redoc", "/v1/ready"):
        assert asyncio.run(call(app, "GET", path)).status_code == 404, path


def test_gateway_mode_relays_a_chat(monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def client(self: OpenAiCompatibleVlmProvider) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"choices": []})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=self.base_url)

    monkeypatch.setattr(OpenAiCompatibleVlmProvider, "async_client", client)
    app = create_gateway_app(registry())

    async def chat() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://gateway"
        ) as http:
            return await http.post(
                "/v1/vlm/chat/completions",
                json={"model": "qwen3-vl-8b", "messages": []},
                headers={"Authorization": f"Bearer {KEY}"},
            )

    assert asyncio.run(chat()).status_code == 200
    assert str(seen[0].url) == "http://127.0.0.1:8080/v1/chat/completions"


def test_gateway_mode_refuses_to_start_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    monkeypatch.delenv("TOOLBOX_ALLOW_UNAUTHENTICATED", raising=False)

    with pytest.raises(ConfigurationError, match="TOOLBOX_API_KEY"):
        create_gateway_app(registry())


@pytest.mark.parametrize(
    "path", ["providers-config.yaml", "deploy/vlm-gateway/providers-config.yaml"]
)
def test_vlm_settings_come_from_the_environment(monkeypatch, path) -> None:
    monkeypatch.setenv("VLM_TIMEOUT_SECONDS", "2700")
    monkeypatch.setenv("VLM_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("VLM_SLOT_WAIT_SECONDS", "60")
    monkeypatch.setenv("VLM_SERVE_URL", "http://gateway:8002/v1/vlm")

    descriptor = ProviderRegistry.from_file(PROJECT_ROOT / path).get("qwen3-vl-8b")
    adapter = OpenAiCompatibleVlmProvider(descriptor)

    # The environment Toolbox's timeout must outlast the gateway's wait; the
    # gateway reads its own from the same variable in its own .env.
    assert descriptor.timeout_seconds == 2700
    assert adapter.slot_wait_seconds == 60
    assert adapter.slots._initial_value == 4  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "path", ["providers-config.yaml", "deploy/vlm-gateway/providers-config.yaml"]
)
def test_vlm_settings_default_without_the_environment(monkeypatch, path) -> None:
    for name in ("VLM_TIMEOUT_SECONDS", "VLM_MAX_CONCURRENCY", "VLM_SLOT_WAIT_SECONDS"):
        monkeypatch.delenv(name, raising=False)

    descriptor = ProviderRegistry.from_file(PROJECT_ROOT / path).get("qwen3-vl-8b")
    adapter = OpenAiCompatibleVlmProvider(descriptor)

    assert descriptor.timeout_seconds == 600
    assert adapter.slot_wait_seconds == 1800
    assert adapter.slots._initial_value == 2  # type: ignore[attr-defined]


def _vlm(provider_id: str, **config: object) -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": provider_id,
            "endpoint": "http://127.0.0.1:8080",
            "capabilities": list(openai_vlm.CAPABILITIES),
            "config": config,
        }
    )


def test_a_gateway_without_a_vlm_provider_is_unhealthy() -> None:
    """No VLM to relay to is a misconfigured gateway, not a healthy one."""
    docling = ProviderDescriptor.model_validate(
        {
            "id": "docling",
            "endpoint": "http://docling:5001",
            "capabilities": ["document.structure.extract"],
        }
    )
    app = create_gateway_app(ProviderRegistry([docling]))

    response = asyncio.run(call(app, "GET", "/v1/vlm/health"))

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "providers": []}


def test_a_vlm_provider_that_cannot_be_built_is_reported_not_raised() -> None:
    """No config.model: a JSON 503 naming the cause, not a text/plain 500."""
    app = create_gateway_app(ProviderRegistry([_vlm("qwen3-vl-8b")]))

    response = asyncio.run(call(app, "GET", "/v1/vlm/health"))

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/json"
    [check] = response.json()["providers"]
    assert check["provider"] == "qwen3-vl-8b" and check["healthy"] is False
    assert "ConfigurationError" in check["detail"] and "config.model" in check["detail"]


def test_vlm_providers_are_probed_concurrently(monkeypatch) -> None:
    """Two slow probes take about one, so a caller's own timeout is not their sum."""
    import time

    from acessilia_toolbox.api import vlm_gateway
    from acessilia_toolbox.core.provider import ProviderHealth

    def slow_health(self: OpenAiCompatibleVlmProvider) -> ProviderHealth:
        time.sleep(0.4)
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            checked_at=vlm_gateway.datetime.now(vlm_gateway.UTC),
        )

    monkeypatch.setattr(OpenAiCompatibleVlmProvider, "health", slow_health)
    monkeypatch.setitem(vlm_gateway.ADAPTERS, "second-vlm", OpenAiCompatibleVlmProvider)
    app = create_gateway_app(
        ProviderRegistry([_vlm("qwen3-vl-8b", model="a"), _vlm("second-vlm", model="b")])
    )

    started = time.monotonic()
    response = asyncio.run(call(app, "GET", "/v1/vlm/health"))
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    providers = {check["provider"] for check in response.json()["providers"]}
    assert providers == {"qwen3-vl-8b", "second-vlm"}
    assert elapsed < 0.75, elapsed
