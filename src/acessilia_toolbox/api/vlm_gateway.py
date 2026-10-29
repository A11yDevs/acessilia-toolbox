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
from collections.abc import AsyncIterator, Awaitable
from contextlib import suppress
from typing import Annotated, Any, TypeVar

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from acessilia_toolbox.api.auth import require_api_key
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

T = TypeVar("T")


class _ClientDisconnectedError(Exception):
    pass


def providers(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


@vlm_router.post("/chat/completions")
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

    if not await _acquire(adapter.slots, request):
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    release = _once(adapter.slots.release)
    client = adapter.async_client()
    try:
        upstream = await _unless_disconnected(
            request, client.send(client.build_request("POST", CHAT_PATH, json=body), stream=stream)
        )
        _reject_credentials(upstream, adapter)
    except _ClientDisconnectedError:
        await client.aclose()
        release()
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    except httpx.TimeoutException as exc:
        await client.aclose()
        release()
        raise ProviderTimeoutError(
            f"{adapter.descriptor.id} did not answer within {adapter.descriptor.timeout_seconds}s",
            provider=adapter.descriptor.id,
        ) from exc
    except httpx.RequestError as exc:
        await client.aclose()
        release()
        raise ProviderUnavailableError(
            f"{adapter.descriptor.id} is not reachable at {adapter.base_url}: {exc}",
            provider=adapter.descriptor.id,
        ) from exc
    except BaseException:
        await client.aclose()
        release()
        raise

    media_type = upstream.headers.get("content-type")
    if not stream:
        await client.aclose()
        release()
        return Response(upstream.content, upstream.status_code, media_type=media_type)

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()
        release()

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


async def _acquire(slots: threading.BoundedSemaphore, request: Request) -> bool:
    """Wait for a slot without blocking the event loop; False if the client left."""
    while not slots.acquire(blocking=False):
        if await request.is_disconnected():
            return False
        await asyncio.sleep(POLL_SECONDS)
    return True


async def _unless_disconnected(request: Request, call: Awaitable[T]) -> T:
    """Await ``call``, cancelling it if the client disconnects first."""
    task = asyncio.ensure_future(call)
    while True:
        done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
        if done:
            return task.result()
        if await request.is_disconnected():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise _ClientDisconnectedError


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


def _once(release: Any) -> Any:
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
