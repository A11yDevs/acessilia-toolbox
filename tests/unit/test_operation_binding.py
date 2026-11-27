"""Bounded request hashing and asynchronous registry notification invariants."""

from __future__ import annotations

import asyncio
import io
import threading
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from acessilia_toolbox.api import rest
from acessilia_toolbox.api.operation_identity import request_fingerprint, upload_identity
from acessilia_toolbox.core.errors import InvalidInputError, UploadTooLargeError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes, fingerprint_stream
from acessilia_toolbox.core.operations import OperationRegistry


def operation(registry):
    return registry.begin("op", task_id="task", attempt_id=None, capability_id="demo.run")[0]


def test_request_binding_is_sealed_and_does_not_expose_fingerprints_in_state():
    registry = OperationRegistry()
    entry = operation(registry)
    first, different = fingerprint_bytes(b"first"), fingerprint_bytes(b"different")
    registry.bind_request(entry, first)
    registry.check_request(entry, first)
    with pytest.raises(InvalidInputError):
        registry.bind_request(entry, different)
    with pytest.raises(InvalidInputError):
        registry.check_request(entry, different)
    assert entry.request_fingerprint == first
    assert "fingerprint" not in str(entry.payload())
    assert first not in repr(entry)
    assert "request_fingerprint" not in repr(entry)


def test_preparation_refusal_wakes_identity_waiters_and_allows_a_new_owner():
    registry = OperationRegistry()
    entry = operation(registry)

    async def run():
        pending = asyncio.create_task(registry.wait_for(entry, request_ready=True))
        await asyncio.sleep(0)
        registry.release(entry, error=RuntimeError("preparation refused"))
        await asyncio.wait_for(pending, 10)
        assert entry.error is not None and entry.request_fingerprint is None
        assert operation(registry) is not entry
        assert not entry.waiters

    asyncio.run(run())


def test_notifications_from_another_thread_wake_async_waiters_and_clean_cancellations():
    registry = OperationRegistry()
    entry = operation(registry)

    async def run():
        waiting = [
            asyncio.create_task(registry.wait_for(entry, request_ready=True)) for _ in range(20)
        ]
        await asyncio.sleep(0)
        thread = threading.Thread(
            target=registry.bind_request, args=(entry, fingerprint_bytes(b"x"))
        )
        thread.start()
        await asyncio.wait_for(asyncio.gather(*waiting), 10)
        thread.join()
        assert not entry.waiters
        cancelled = asyncio.create_task(registry.wait_for(entry))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert not entry.waiters
        pending = asyncio.create_task(registry.wait_for(entry))
        await asyncio.sleep(0)
        registry.finish(entry, "finished", result={"ok": True})
        await asyncio.wait_for(pending, 10)
        assert not entry.waiters and entry.done_event.is_set()

    asyncio.run(run())


def test_async_preparation_abort_releases_operation_and_slot(monkeypatch):
    registry, slots = OperationRegistry(), threading.BoundedSemaphore(1)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                operations=registry,
                execution_slots=slots,
            )
        )
    )

    async def abort(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(rest, "_operation_input", abort)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            rest.store_artifact(
                None,
                None,
                request,
                operation_id="op",
                task_id="task",
                attempt_id=None,
            )
        )
    assert registry.get("op") is None
    assert slots.acquire(blocking=False)


def test_upload_hash_is_chunk_bounded_rewinds_and_refuses_overflow():
    class BoundedReads(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.requested = []

        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            self.requested.append(size)
            return super().read(size)

    stream = BoundedReads(b"12345")
    upload = UploadFile(stream, filename="doc.pdf", size=1)  # metadata cannot defeat the bound
    with pytest.raises(UploadTooLargeError):
        upload_identity(upload, max_bytes=3, default_filename="upload")
    assert stream.tell() == 0 and stream.requested == [4]
    assert upload_identity(upload, max_bytes=5, default_filename="upload")[
        "content"
    ] == fingerprint_bytes(b"12345")
    assert stream.tell() == 0
    with pytest.raises(ValueError):
        fingerprint_stream(stream, max_bytes=-1)


def test_request_parameters_are_canonical_and_types_and_array_order_stay_distinct():
    assert request_fingerprint({"parameters": {"a": 1, "b": False}}) == request_fingerprint(
        {"parameters": {"b": False, "a": 1}}
    )
    assert request_fingerprint({"v": True}) != request_fingerprint({"v": 1})
    assert request_fingerprint({"v": [1, 2]}) != request_fingerprint({"v": [2, 1]})
    for value in (float("nan"), float("inf"), "\ud800"):
        with pytest.raises(InvalidInputError):
            request_fingerprint({"v": value})
