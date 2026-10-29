"""OpenAI-compatible chat gateway to the VLM providers.

The model server is simulated with httpx.MockTransport; each test asserts what
the gateway relays and that the provider's slot is free again afterwards.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from acessilia_toolbox.api import vlm_gateway
from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import openai_vlm
from acessilia_toolbox.providers.openai_vlm import OpenAiCompatibleVlmProvider

TOOLBOX_KEY = "toolbox-key"
VLM_KEY = "vlm-key"
PATH = "/v1/vlm/chat/completions"
CHAT = {"model": "qwen3-vl-8b", "messages": [{"role": "user", "content": "hi"}]}
ANSWER = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _fresh_limiters(monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", TOOLBOX_KEY)
    monkeypatch.setattr(vlm_gateway, "POLL_SECONDS", 0.01)
    openai_vlm._SEMAPHORES.clear()
    yield
    openai_vlm._SEMAPHORES.clear()


def vlm_descriptor(timeout_seconds: float = 5.0, **config: Any) -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "qwen3-vl-8b",
            "version": "test-weights",
            "endpoint": "http://vlm:8080",
            "capabilities": list(openai_vlm.CAPABILITIES),
            "timeout_seconds": timeout_seconds,
            "config": {"model": "served-name", "api_key": VLM_KEY, **config},
        }
    )


def app_with(
    handler: Handler,
    monkeypatch,
    *,
    timeout_seconds: float = 5.0,
    extra: tuple[ProviderDescriptor, ...] = (),
    **config: Any,
):
    def client(self: OpenAiCompatibleVlmProvider) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=self.base_url,
            headers=self._headers(),
        )

    monkeypatch.setattr(OpenAiCompatibleVlmProvider, "async_client", client)
    descriptors = [vlm_descriptor(timeout_seconds, **config), *extra]
    return create_app(CapabilityRegistry([]), ProviderRegistry(descriptors))


async def post(app, body: Any, *, key: str | None = TOOLBOX_KEY) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://toolbox"
    ) as client:
        return await client.post(PATH, json=body, headers=headers)


def slots_free(max_concurrency: int = 4) -> bool:
    slots = openai_vlm._SEMAPHORES["qwen3-vl-8b"]
    taken = [slots.acquire(blocking=False) for _ in range(max_concurrency)]
    for ok in taken:
        if ok:
            slots.release()
    return all(taken)


def test_relays_the_chat_with_the_providers_model_and_key(monkeypatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ANSWER)

    response = asyncio.run(post(app_with(handler, monkeypatch), CHAT))

    assert response.status_code == 200
    assert response.json() == ANSWER
    (request,) = seen
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {VLM_KEY}"
    assert json.loads(request.content) == {**CHAT, "model": "served-name"}
    assert slots_free()


def test_relays_a_streamed_chat(monkeypatch) -> None:
    events = b'data: {"choices":[]}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=events, headers={"content-type": "text/event-stream"})

    response = asyncio.run(post(app_with(handler, monkeypatch), {**CHAT, "stream": True}))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.content == events
    assert slots_free()


def test_a_truthy_non_boolean_stream_is_sent_as_false(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=ANSWER)

    response = asyncio.run(post(app_with(handler, monkeypatch), {**CHAT, "stream": 1}))

    # The relay buffers non-streamed answers, so the server must not stream.
    assert response.json() == ANSWER
    assert seen[0]["stream"] is False


class _Gated(httpx.AsyncByteStream):
    """Server-sent events whose second chunk waits for the test."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate

    async def __aiter__(self):
        yield b"data: 1\n\n"
        await self.gate.wait()
        yield b"data: [DONE]\n\n"


async def drive(app, body: dict[str, Any], on_message) -> None:
    """Call the ASGI app directly: ASGITransport buffers the whole response."""
    payload = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PATH,
        "raw_path": PATH.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"toolbox"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
            (b"authorization", f"Bearer {TOOLBOX_KEY}".encode()),
        ],
        "client": ("test", 1),
        "server": ("toolbox", 80),
    }
    sent = False
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await never.wait()
        return {"type": "http.disconnect"}

    await app(scope, receive, on_message)


def test_a_streamed_chat_is_relayed_chunk_by_chunk_holding_its_slot(monkeypatch) -> None:
    gate = asyncio.Event()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Gated(gate)
        )

    app = app_with(handler, monkeypatch, max_concurrency=1)
    chunks: list[bytes] = []
    held_while_streaming: list[bool] = []

    async def on_message(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            if len(chunks) == 1:
                # The first event reached the client before the second exists.
                held_while_streaming.append(not slots_free(1))
                gate.set()

    # A buffering relay would wait for the gate forever instead of failing.
    asyncio.run(asyncio.wait_for(drive(app, {**CHAT, "stream": True}, on_message), 5))

    assert chunks == [b"data: 1\n\n", b"data: [DONE]\n\n"]
    assert held_while_streaming == [True]
    assert slots_free(1)


def test_relays_server_errors_as_they_are(monkeypatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "context size exceeded"}})

    response = asyncio.run(post(app_with(handler, monkeypatch), CHAT))

    assert response.status_code == 400
    assert response.json() == {"error": {"message": "context size exceeded"}}
    assert slots_free()


def test_requires_the_toolbox_key(monkeypatch) -> None:
    app = app_with(lambda _: httpx.Response(200, json=ANSWER), monkeypatch)

    assert asyncio.run(post(app, CHAT, key=None)).status_code == 401
    assert asyncio.run(post(app, CHAT, key="wrong")).status_code == 401


def test_rejects_unknown_models_and_bodies(monkeypatch) -> None:
    app = app_with(lambda _: httpx.Response(200, json=ANSWER), monkeypatch)

    assert asyncio.run(post(app, {**CHAT, "model": "gpt-4"})).status_code == 404
    assert asyncio.run(post(app, ["not", "an", "object"])).status_code == 400
    assert asyncio.run(post(app, {"messages": []})).status_code == 400


def test_rejects_a_provider_that_is_not_an_openai_compatible_vlm(monkeypatch) -> None:
    text = ProviderDescriptor(id="pure-text", capabilities=["text.normalize"])
    app = app_with(lambda _: httpx.Response(200, json=ANSWER), monkeypatch, extra=(text,))

    response = asyncio.run(post(app, {**CHAT, "model": "pure-text"}))

    assert response.status_code == 400
    assert "not an OpenAI-compatible VLM" in response.text


def test_server_rejecting_the_vlm_key_is_a_provider_error(monkeypatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API Key"})

    response = asyncio.run(post(app_with(handler, monkeypatch), CHAT))

    assert response.status_code == 502
    assert "credentials" in response.text
    assert slots_free()


def test_unreachable_server_is_unavailable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    response = asyncio.run(post(app_with(handler, monkeypatch), CHAT))

    assert response.status_code == 503
    assert slots_free()


def test_slow_server_is_a_timeout(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    response = asyncio.run(post(app_with(handler, monkeypatch), CHAT))

    assert response.status_code == 504
    assert slots_free()


def test_waits_for_a_slot_shared_with_the_capabilities(monkeypatch) -> None:
    async def run() -> int:
        called = asyncio.Event()

        def handler(_: httpx.Request) -> httpx.Response:
            called.set()
            return httpx.Response(200, json=ANSWER)

        app = app_with(handler, monkeypatch, max_concurrency=1)
        # The capability path takes the same limiter, as a running page OCR would.
        busy = openai_vlm._semaphore("qwen3-vl-8b", 1)
        assert busy.acquire(blocking=False)
        waiting = asyncio.create_task(post(app, CHAT))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(called.wait()), 0.05)
        busy.release()
        response = await waiting
        assert called.is_set()
        return response.status_code

    assert asyncio.run(run()) == 200
    assert slots_free(1)


def test_waiting_for_a_slot_is_bounded_by_the_providers_timeout(monkeypatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=ANSWER)

    app = app_with(handler, monkeypatch, timeout_seconds=0.05, max_concurrency=1)
    busy = openai_vlm._semaphore("qwen3-vl-8b", 1)
    assert busy.acquire(blocking=False)

    response = asyncio.run(post(app, CHAT))

    assert response.status_code == 504
    assert "no free slot" in response.text
    assert not calls
    busy.release()
    assert slots_free(1)


class _Gone:
    async def is_disconnected(self) -> bool:
        return True


def test_a_client_that_leaves_cancels_the_upstream_call() -> None:
    cancelled = asyncio.Event()

    async def generation() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def run() -> None:
        with pytest.raises(vlm_gateway._ClientDisconnectedError):
            await vlm_gateway._unless_disconnected(_Gone(), generation())  # type: ignore[arg-type]
        assert cancelled.is_set()

    asyncio.run(run())


def test_a_cancelled_request_cancels_the_upstream_call() -> None:
    class Here:
        async def is_disconnected(self) -> bool:
            return False

    started, cancelled = asyncio.Event(), asyncio.Event()

    async def generation() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def run() -> None:
        # A server shutdown cancels the request task from outside.
        request = asyncio.create_task(
            vlm_gateway._unless_disconnected(Here(), generation())  # type: ignore[arg-type]
        )
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert cancelled.is_set()

    asyncio.run(run())


def test_a_client_that_leaves_while_waiting_takes_no_slot() -> None:
    adapter = OpenAiCompatibleVlmProvider(vlm_descriptor(max_concurrency=1))
    assert adapter.slots.acquire(blocking=False)

    acquired = asyncio.run(vlm_gateway._acquire(adapter, _Gone()))  # type: ignore[arg-type]

    assert acquired is False
    adapter.slots.release()
    assert adapter.slots.acquire(blocking=False)
