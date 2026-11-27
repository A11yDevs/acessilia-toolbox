"""Keep request-owned resources alive until their blocking call actually ends."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import ParamSpec, TypeVar, cast

import anyio
from starlette.concurrency import run_in_threadpool

P = ParamSpec("P")
T = TypeVar("T")


async def run_blocking(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Delay coroutine cancellation until its worker releases streams and slots.

    AnyIO shields its own cancel scopes, but raw ``asyncio.Task.cancel`` can
    still abandon a threadpool await. Shield a separate waiter and drain it on
    every cancellation before letting the owner's cleanup run. Capture control
    exceptions inside the worker so a child task cannot terminate the event loop.
    The owner still receives its original interruption after the worker ends.
    """
    call = partial(func, *args, **kwargs)

    def invoke() -> tuple[T | None, BaseException | None]:
        try:
            return call(), None
        except BaseException as exc:
            return None, exc

    worker = asyncio.create_task(run_in_threadpool(invoke))
    interrupted: asyncio.CancelledError | None = None
    with anyio.CancelScope(shield=True):
        while True:
            try:
                result, error = await asyncio.shield(worker)
                break
            except asyncio.CancelledError as exc:
                if worker.cancelled():
                    # Event-loop shutdown can also cancel the shielded task itself.
                    raise
                interrupted = exc
    if interrupted is not None:
        raise interrupted
    if error is not None:
        raise error
    return cast(T, result)
