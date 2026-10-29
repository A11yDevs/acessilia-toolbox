"""OpenAI-compatible chat gateway to the vision-language model servers.

The agentic core describes images and transcribes tables with its own prompts
through an OpenAI-compatible client. Pointing that client here, instead of at
the model server, puts its requests under the same per-provider limiter as the
VLM capabilities, so the server never receives more requests than it has
parallel slots, and the model server's key stays with the Toolbox.

``POST /v1/vlm/chat/completions`` takes a chat-completions body whose
``model`` is the id of an OpenAI-compatible VLM provider (``qwen3-vl-8b``),
waits for one of the provider's slots, and relays the request, streamed or not,
with the provider's own model id and key. The server's answer is returned as
is. A client that disconnects, while waiting or while the server generates,
frees its slot and closes the upstream connection, so llama-server stops the
generation instead of finishing it for nobody.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Annotated, Any, TypeVar

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from acessilia_toolbox.api.auth import require_api_key
from acessilia_toolbox.api.rest import ERROR_RESPONSES, providers
from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderRegistry
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.openai_vlm import CHAT_PATH, OpenAiCompatibleVlmProvider

vlm_router = APIRouter(
    prefix="/v1/vlm",
    dependencies=[Depends(require_api_key)],
    tags=["vlm"],
)

# How often a waiting request checks whether its client is still there.
POLL_SECONDS = 0.5

# Status a disconnected client never sees; it only ends the request.
CLIENT_CLOSED_REQUEST = 499

GATEWAY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The server's chat completion, relayed as is; server-sent "
        "events when the body sets stream",
        "content": {"application/json": {}, "text/event-stream": {}},
    },
    **{status: ERROR_RESPONSES[status] for status in (400, 404, 502, 503, 504)},
    CLIENT_CLOSED_REQUEST: {"description": "The client disconnected; it never sees this status"},
}

CHAT_REQUEST_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "description": "OpenAI chat-completions request, relayed as is "
                    "except for model",
                    "required": ["model", "messages"],
                    "properties": {
                        "model": {"type": "string", "description": "VLM provider id"},
                        "messages": {"type": "array", "items": {"type": "object"}},
                        "stream": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": True,
                }
            }
        },
    }
}

T = TypeVar("T")


class _ClientDisconnectedError(Exception):
    pass


@vlm_router.post("/chat/completions", responses=GATEWAY_RESPONSES, openapi_extra=CHAT_REQUEST_BODY)
async def chat_completions(
    request: Request,
    registry: Annotated[ProviderRegistry, Depends(providers)],
) -> Response:
    try:
        body: Any = await request.json()
    except ValueError:
        raise InvalidInputError("request body must be a JSON object") from None
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise InvalidInputError("request body must be a JSON object with a string model")

    adapter = create_adapter(registry.get(body["model"]))
    if not isinstance(adapter, OpenAiCompatibleVlmProvider):
        raise InvalidInputError(
            f"provider {body['model']} is not an OpenAI-compatible VLM",
            provider=body["model"],
        )
    body["model"] = adapter.config["model"]
    stream = body.get("stream") is True
    if "stream" in body:
        # The server would stream a truthy non-boolean that the relay buffers.
        body["stream"] = stream

    if not await _acquire(adapter, request):
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    release = _once(adapter.slots.release)
    try:
        client = adapter.async_client()
    except BaseException:
        release()
        raise

    async def discard() -> None:
        try:
            await client.aclose()
        finally:
            release()

    try:
        upstream = await _unless_disconnected(
            request, client.send(client.build_request("POST", CHAT_PATH, json=body), stream=stream)
        )
        _reject_credentials(upstream, adapter)
    except _ClientDisconnectedError:
        await discard()
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    except httpx.TimeoutException as exc:
        await discard()
        raise ProviderTimeoutError(
            f"{adapter.descriptor.id} did not answer within {adapter.descriptor.timeout_seconds}s",
            provider=adapter.descriptor.id,
        ) from exc
    except httpx.RequestError as exc:
        await discard()
        raise ProviderUnavailableError(
            f"{adapter.descriptor.id} is not reachable at {adapter.base_url}: {exc}",
            provider=adapter.descriptor.id,
        ) from exc
    except BaseException:
        await discard()
        raise

    media_type = upstream.headers.get("content-type")
    if not stream:
        await discard()
        return Response(upstream.content, upstream.status_code, media_type=media_type)

    async def close() -> None:
        try:
            await upstream.aclose()
        finally:
            await discard()

    async def relay() -> AsyncIterator[bytes]:
        try:
            # Decoded bytes: the server's content-encoding is not relayed.
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await close()

    # The background task also closes a stream the response never started.
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        media_type=media_type,
        background=BackgroundTask(close),
    )


async def _acquire(adapter: OpenAiCompatibleVlmProvider, request: Request) -> bool:
    """Wait for a slot without blocking the event loop; False if the client left.

    The wait is bounded by the provider's ``timeout_seconds``. A non-blocking
    acquire does not queue behind a capability thread blocked on the same
    semaphore, so under load the gateway may take a slot first.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + adapter.descriptor.timeout_seconds
    while not adapter.slots.acquire(blocking=False):
        if await request.is_disconnected():
            return False
        if loop.time() >= deadline:
            raise ProviderTimeoutError(
                f"{adapter.descriptor.id} had no free slot within "
                f"{adapter.descriptor.timeout_seconds}s",
                provider=adapter.descriptor.id,
            )
        await asyncio.sleep(POLL_SECONDS)
    return True


async def _unless_disconnected(request: Request, call: Awaitable[T]) -> T:
    """Await ``call``, cancelling it if the client disconnects or the request is cancelled."""
    task = asyncio.ensure_future(call)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                raise _ClientDisconnectedError
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _reject_credentials(upstream: httpx.Response, adapter: OpenAiCompatibleVlmProvider) -> None:
    # Relayed as is, the server's 401 would read as the caller's Toolbox key
    # being wrong; it is the Toolbox's VLM key.
    if upstream.status_code in (401, 403):
        raise ProviderExecutionError(
            f"{adapter.descriptor.id} rejected the Toolbox's credentials "
            f"(status {upstream.status_code}); check the provider's api_key",
            provider=adapter.descriptor.id,
            status_code=upstream.status_code,
        )


def _once(release: Callable[[], None]) -> Callable[[], None]:
    lock = threading.Lock()
    released = False

    def run() -> None:
        nonlocal released
        with lock:
            if released:
                return
            released = True
        release()

    return run
