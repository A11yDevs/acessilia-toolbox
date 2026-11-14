"""Operation identifiers, joining and cooperative cancellation."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from tests.integration.test_rest_api import CAPABILITY, MANIFEST, PDF, StubProvider

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.artifact import NullCache
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError, OperationNotFoundError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.operations import OperationRegistry
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry

PAYLOAD = b"%PDF-operation"


def _registries():
    capabilities = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    providers = ProviderRegistry(
        [ProviderDescriptor(id="stub", capabilities=[CAPABILITY])]
    )
    return capabilities, providers


def test_begin_retry_joins_the_same_operation():
    registry = OperationRegistry()
    operation, owner = registry.begin(
        "op-1", task_id="task-1", attempt_id="attempt-1", capability_id=CAPABILITY
    )
    assert owner is True

    joined, owner = registry.begin(
        "op-1", task_id="task-1", attempt_id="attempt-2", capability_id=CAPABILITY
    )
    assert owner is False
    assert joined is operation
    assert joined.attempt_id == "attempt-1"


def test_cancel_requests_only_a_running_operation():
    registry = OperationRegistry()
    operation, _ = registry.begin(
        "op-1", task_id="task-1", attempt_id=None, capability_id=CAPABILITY
    )

    assert registry.cancel("op-1", task_id="task-1") is operation
    assert operation.state == "cancel_requested"
    assert operation.cancel_requested is True

    registry.finish(operation, "finished")
    assert operation.state == "cancelled"
    assert registry.cancel("op-1", task_id="task-1") is operation
    assert operation.state == "cancelled"


def test_cancel_rejects_unknown_or_other_task():
    registry = OperationRegistry()
    registry.begin("op-1", task_id="task-1", attempt_id=None, capability_id=CAPABILITY)
    with pytest.raises(OperationNotFoundError):
        registry.cancel("op-missing", task_id="task-1")
    with pytest.raises(OperationNotFoundError):
        registry.cancel("op-1", task_id="task-2")


def test_finished_operation_can_be_joined_after_the_response_was_lost():
    registry = OperationRegistry()
    operation, _ = registry.begin(
        "op-1", task_id="task-1", attempt_id="attempt-1", capability_id=CAPABILITY
    )
    registry.finish(operation, "finished", result={"answer": 42})

    joined, owner = registry.begin(
        "op-1", task_id="task-1", attempt_id="attempt-2", capability_id=CAPABILITY
    )
    assert owner is False
    assert joined.result == {"answer": 42}
    assert joined.done_event.is_set()


def test_route_cancels_a_blocked_execution(monkeypatch):
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    capabilities, providers = _registries()
    app = create_app(capabilities, providers)
    entered, release = threading.Event(), threading.Event()

    class Runner:
        def execute(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(3)
            from acessilia_toolbox.core.errors import OperationCancelledError

            raise OperationCancelledError("cancelled by test", operation_id="op-http")

    app.state.executor = Runner()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://audit"
        ) as client:
            headers = {
                "X-Acessilia-Task-Id": "task-http",
                "X-Acessilia-Operation-Id": "op-http",
                "X-Acessilia-Attempt-Id": "attempt-1",
            }
            execution = asyncio.create_task(
                client.post(
                    f"/v1/capabilities/{CAPABILITY}:execute",
                    files={"file": ("doc.pdf", PAYLOAD, PDF)},
                    headers=headers,
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await client.post(
                    "/v1/operations/op-http:cancel",
                    headers={"X-Acessilia-Task-Id": "task-http"},
                )
                assert response.status_code == 200
                assert response.json() == {
                    "operation_id": "op-http",
                    "state": "cancel_requested",
                    "cancel_requested": True,
                }
            finally:
                release.set()
                result = await execution
            assert result.status_code == 409
            assert result.json()["code"] == "operation_cancelled"

            unknown = await client.post(
                "/v1/operations/op-missing:cancel",
                headers={"X-Acessilia-Task-Id": "task-http"},
            )
            assert unknown.status_code == 404

    asyncio.run(run())


def test_cancelled_execution_does_not_store_or_cache(monkeypatch):
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    capabilities, providers = _registries()
    entered, release = threading.Event(), threading.Event()

    class BlockingProvider(StubProvider):
        def execute(self, *args, **kwargs) -> ExtractionResult:
            entered.set()
            assert release.wait(3)
            return super().execute(*args, **kwargs)

    class CountingCache(NullCache):
        def __init__(self) -> None:
            self.puts: list[str] = []

        def put(self, key: str, value: Mapping[str, Any]) -> None:
            self.puts.append(key)

    cache = CountingCache()
    executor = CapabilityExecutor(
        capabilities,
        providers,
        lambda descriptor: BlockingProvider(descriptor),
        cache=cache,
    )
    operation, _ = OperationRegistry().begin(
        "op-exec", task_id="task-exec", attempt_id="attempt-1", capability_id=CAPABILITY
    )

    result: list[BaseException] = []

    def run() -> None:
        try:
            executor.execute(
                CAPABILITY,
                PAYLOAD,
                filename="doc.pdf",
                media_type=PDF,
                operation=operation,
            )
        except BaseException as exc:
            result.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(2)
    operation.cancel_event.set()
    release.set()
    worker.join(3)

    assert result
    assert result[0].__class__.__name__ == "OperationCancelledError"
    assert cache.puts == []


def test_invalid_identifier_is_rejected_before_state_is_created():
    registry = OperationRegistry()
    with pytest.raises(InvalidInputError):
        registry.begin(
            "bad id with spaces",
            task_id="task-1",
            attempt_id=None,
            capability_id=CAPABILITY,
        )


def test_retry_joins_a_running_operation_without_a_second_slot(monkeypatch):
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    capabilities, providers = _registries()
    app = create_app(capabilities, providers)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    class BlockingProvider(StubProvider):
        def execute(self, *args, **kwargs) -> ExtractionResult:
            nonlocal calls
            calls += 1
            entered.set()
            assert release.wait(3)
            return super().execute(*args, **kwargs)

    app.state.executor = CapabilityExecutor(
        capabilities, providers, lambda descriptor: BlockingProvider(descriptor)
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://audit"
        ) as client:
            headers = {
                "X-Acessilia-Task-Id": "task-join",
                "X-Acessilia-Operation-Id": "op-join",
                "X-Acessilia-Attempt-Id": "attempt-1",
            }
            first = asyncio.create_task(
                client.post(
                    f"/v1/capabilities/{CAPABILITY}:execute",
                    files={"file": ("doc.pdf", PAYLOAD, PDF)},
                    headers=headers,
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                second = asyncio.create_task(
                    client.post(
                        f"/v1/capabilities/{CAPABILITY}:execute",
                        files={"file": ("doc.pdf", PAYLOAD, PDF)},
                        headers={**headers, "X-Acessilia-Attempt-Id": "attempt-2"},
                    )
                )
                await asyncio.sleep(0.1)
                assert calls == 1
            finally:
                release.set()
                one, two = await first, await second
            assert one.status_code == two.status_code == 200
            assert one.json() == two.json()
            assert calls == 1

    asyncio.run(run())
