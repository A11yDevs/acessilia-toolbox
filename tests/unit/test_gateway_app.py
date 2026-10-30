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

    assert paths == {"/v1/health", "/v1/vlm/chat/completions", "/v1/vlm/health"}
    assert asyncio.run(call(app, "GET", "/v1/health")).status_code == 200
    assert asyncio.run(call(app, "GET", "/v1/capabilities")).status_code == 404


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

    if path == "providers-config.yaml":
        # The environment Toolbox's timeout must outlast the gateway's wait.
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
