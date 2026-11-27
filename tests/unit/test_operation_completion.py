"""Terminal decisions, interrupted owners and independent retry lifetimes."""

from __future__ import annotations

import asyncio
import io
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import UploadFile
from tests.unit.test_operation_request_identity import ENDPOINT, HEADERS, PAYLOAD, PDF, invoke
from tests.unit.test_operation_request_identity import client as client

from acessilia_toolbox.api import rest
from acessilia_toolbox.api.blocking import run_blocking
from acessilia_toolbox.core.errors import (
    OperationCancelledError,
    ProviderUnavailableError,
    ToolboxError,
)
from acessilia_toolbox.core.operations import OperationRegistry
from acessilia_toolbox.providers.storage import FilesystemArtifactStore


def entry(registry):
    return registry.begin("op", task_id="task", attempt_id=None, capability_id="demo.run")[0]


def test_unknown_size_input_check_is_drained_before_preparation_slot_release():
    registry, slots = OperationRegistry(), threading.BoundedSemaphore(1)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(operations=registry, execution_slots=slots))
    )
    release = threading.Event()

    async def run():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        class BlockingInput(io.BytesIO):
            def read(self, size):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(10)
                assert not self.closed
                return super().read(size)

        file = UploadFile(BlockingInput(b"payload"), filename="doc.pdf", size=None)
        owner = asyncio.create_task(
            rest.store_artifact(
                None, file, request, operation_id="op", task_id="task", attempt_id=None
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 10)
            operation = registry.get("op")
            owner.cancel()
            await asyncio.sleep(0)
            assert not owner.done()
            assert not slots.acquire(blocking=False)
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
            file.file.close()
        assert registry.get("op") is None
        assert isinstance(operation.error, ToolboxError) and operation.error.http_status == 503
        assert slots.acquire(blocking=False)

    asyncio.run(run())


@pytest.mark.parametrize("error_type", [asyncio.CancelledError, SystemExit, KeyboardInterrupt])
def test_worker_control_exceptions_reach_only_the_owning_coroutine(error_type):
    def interrupted():
        raise error_type()

    async def run():
        with pytest.raises(error_type):
            await run_blocking(interrupted)

    asyncio.run(run())


@pytest.mark.parametrize("cancel_first", [True, False])
def test_terminal_decision_is_authoritative_and_cancelled_results_are_not_retained(cancel_first):
    registry = OperationRegistry()
    operation = entry(registry)
    if cancel_first:
        registry.cancel("op", task_id="task")
    final = registry.finish(operation, "finished", result={"ok": True})
    registry.cancel("op", task_id="task")
    assert final == ("cancelled" if cancel_first else "finished")
    assert operation.result == (None if cancel_first else {"ok": True})
    if cancel_first:
        assert isinstance(operation.error, OperationCancelledError)
    else:
        assert operation.error is None


@pytest.mark.parametrize("route", ["execute", "store"])
def test_cancel_between_last_check_and_finish_refuses_owner_and_retry(
    client, monkeypatch, tmp_path, route
):
    registry = client.app.state.operations
    original = registry.finish
    client.app.state.executor._store = FilesystemArtifactStore(tmp_path)

    def finish(operation, state, **kwargs):
        if state == "finished":
            registry.cancel(operation.operation_id, task_id=operation.task_id)
        return original(operation, state, **kwargs)

    monkeypatch.setattr(registry, "finish", finish)

    def post():
        if route == "execute":
            return invoke(client)
        return client.post(
            "/v1/artifacts", headers=HEADERS, files={"file": ("doc.pdf", PAYLOAD, PDF)}
        )

    first = post()
    assert first.status_code == 409
    assert post().json() == first.json()
    operation = registry.get(HEADERS["X-Acessilia-Operation-Id"])
    assert operation.state == "cancelled" and operation.result is None


@pytest.mark.parametrize("route", ["execute", "store"])
def test_cancel_wins_over_a_concurrent_provider_failure_for_owner_and_retry(
    client, monkeypatch, route
):
    registry = client.app.state.operations

    def fail(*args, **kwargs):
        registry.cancel(HEADERS["X-Acessilia-Operation-Id"], task_id=HEADERS["X-Acessilia-Task-Id"])
        raise ProviderUnavailableError("provider failed after cancellation")

    monkeypatch.setattr(
        client.app.state.executor,
        "execute" if route == "execute" else "store_artifact_stream",
        fail,
    )

    def post():
        if route == "execute":
            return invoke(client)
        return client.post(
            "/v1/artifacts", headers=HEADERS, files={"file": ("doc.pdf", PAYLOAD, PDF)}
        )

    first = post()
    assert first.status_code == 409
    assert first.json()["code"] == "operation_cancelled"
    assert post().json() == first.json()
    operation = registry.get(HEADERS["X-Acessilia-Operation-Id"])
    assert isinstance(operation.error, OperationCancelledError)
    assert operation.result is None


@pytest.mark.parametrize("preparation", [True, False])
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, SystemExit, KeyboardInterrupt])
def test_control_exceptions_are_not_retained_for_another_coroutine(preparation, error_type):
    registry = OperationRegistry()
    operation = entry(registry)
    if preparation:
        registry.release(operation, error=error_type())
    else:
        registry.finish(operation, "failed", error=error_type())
    assert isinstance(operation.error, ToolboxError)
    assert operation.error.http_status == (503 if preparation else 502)
    assert operation.error.__cause__ is None
    assert operation.error.__traceback__ is None
    assert (registry.get("op") is None) == preparation


@pytest.mark.parametrize("signal", ["bind", "finish", "release"])
def test_loop_shutdown_during_notification_does_not_break_owner_or_other_waiters(
    monkeypatch, signal
):
    registry = OperationRegistry()
    operation = entry(registry)
    closed_loop = asyncio.new_event_loop()
    lost = closed_loop.create_future()
    lost.add_done_callback(lambda _: None)
    schedule = closed_loop.call_soon_threadsafe

    def shutdown(*args):
        closed_loop.close()
        return schedule(*args)

    monkeypatch.setattr(closed_loop, "call_soon_threadsafe", shutdown)
    ready = signal == "bind"
    operation.waiters.add((ready, closed_loop, lost))

    async def run():
        pending = asyncio.create_task(registry.wait_for(operation, request_ready=ready))
        await asyncio.sleep(0)  # register before the deterministic shutdown barrier
        try:
            if signal == "bind":
                registry.bind_request(operation, "digest")
            elif signal == "finish":
                registry.finish(operation, "finished", result="ok")
            else:
                registry.release(operation, error=RuntimeError("refused"))
            await asyncio.wait_for(pending, 10)
            assert lost.cancelled()
            assert not operation.waiters
        finally:
            pending.cancel()
            if not closed_loop.is_closed():
                closed_loop.close()

    asyncio.run(run())


@pytest.mark.parametrize("route", ["execute", "store"])
def test_disconnected_owner_keeps_worker_slot_and_stream_until_work_finishes(
    client, monkeypatch, tmp_path, route
):
    runner = client.app.state.executor
    runner._store = FilesystemArtifactStore(tmp_path)
    semaphore = threading.BoundedSemaphore(1)
    slots = Mock(wraps=semaphore)
    client.app.state.execution_slots = slots
    registry = client.app.state.operations
    method = "execute" if route == "execute" else "store_artifact_stream"
    original = getattr(runner, method)
    release = threading.Event()
    calls = []

    async def run():
        loop = asyncio.get_running_loop()
        entered, joined = asyncio.Event(), asyncio.Event()
        wait = registry.wait_for
        joining = 0

        def blocked(*args, **kwargs):
            stream = args[1] if route == "execute" else args[0]
            calls.append(stream)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            assert not stream.closed
            return original(*args, **kwargs)

        async def waiting(operation, *, request_ready=False):
            nonlocal joining
            if not request_ready:
                joining += 1
                if joining == 2:
                    joined.set()
            await wait(operation, request_ready=request_ready)

        monkeypatch.setattr(runner, method, blocked)
        monkeypatch.setattr(registry, "wait_for", waiting)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(client.app), base_url="http://test"
        ) as api:

            async def post():
                return await api.post(
                    ENDPOINT if route == "execute" else "/v1/artifacts",
                    headers=HEADERS,
                    files={"file": ("doc.pdf", PAYLOAD, PDF)},
                    data={"provider": "stub", "version": "1"},
                )

            owner = asyncio.create_task(post())
            retries = []
            try:
                await asyncio.wait_for(entered.wait(), 10)
                retries = [asyncio.create_task(post()) for _ in range(2)]
                await asyncio.wait_for(joined.wait(), 10)
                retries[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await retries[0]
                owner.cancel()
                await asyncio.sleep(0)  # deliver the raw asyncio cancellation
                owner.cancel()
                await asyncio.sleep(0)  # repeated cancellation cannot abandon the worker
                assert not owner.done()
                assert not retries[1].done()
                assert not semaphore.acquire(blocking=False)
                operation = registry.get(HEADERS["X-Acessilia-Operation-Id"])
                assert operation.state == "running"
                assert slots.release.call_count == 0
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await owner
            response = await asyncio.wait_for(retries[1], 10)
            assert response.status_code == 502
            assert response.json()["code"] == "provider_execution_failed"
            assert (await post()).json() == response.json()
            assert operation.state == "failed" and operation.result is None
            assert len(calls) == 1 and slots.release.call_count == 1
            assert semaphore.acquire(blocking=False)

    asyncio.run(run())


def test_owner_abort_before_binding_wakes_retry_with_typed_refusal_and_allows_recovery(
    client, monkeypatch, tmp_path
):
    client.app.state.executor._store = FilesystemArtifactStore(tmp_path)
    original = rest._operation_input
    registry = client.app.state.operations

    async def run():
        entered, joined = asyncio.Event(), asyncio.Event()
        wait = registry.wait_for
        first = True

        async def prepare(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                entered.set()
                await asyncio.Event().wait()
            return await original(*args, **kwargs)

        async def waiting(operation, *, request_ready=False):
            joined.set()
            await wait(operation, request_ready=request_ready)

        monkeypatch.setattr(rest, "_operation_input", prepare)
        monkeypatch.setattr(registry, "wait_for", waiting)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(client.app), base_url="http://test"
        ) as api:

            async def post():
                return await api.post(
                    "/v1/artifacts",
                    headers=HEADERS,
                    files={"file": ("doc.pdf", PAYLOAD, PDF)},
                )

            owner = asyncio.create_task(post())
            await asyncio.wait_for(entered.wait(), 10)
            retry = asyncio.create_task(post())
            await asyncio.wait_for(joined.wait(), 10)
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            response = await asyncio.wait_for(retry, 10)
            assert response.status_code == 503
            assert response.json()["code"] == "provider_unavailable"
            assert (await post()).status_code == 200

    asyncio.run(run())
