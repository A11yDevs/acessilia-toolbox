"""A retained operation result belongs to one complete HTTP execution request."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from unittest.mock import Mock

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_rest_api import CAPABILITY, MANIFEST, PDF, StubProvider

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import ProviderTimeoutError, ProviderUnavailableError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

HEADERS = {
    "Authorization": "Bearer operation-test",
    "X-Acessilia-Task-Id": "task-identity",
    "X-Acessilia-Operation-Id": "op-identity",
    "X-Acessilia-Attempt-Id": "attempt-1",
}
PAYLOAD = b"%PDF-identity"
ENDPOINT = f"/v1/capabilities/{CAPABILITY}:execute"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "operation-test")
    capabilities = CapabilityRegistry(
        [
            CapabilityManifest.model_validate(MANIFEST),
            CapabilityManifest.model_validate({**MANIFEST, "version": 2}),
        ]
    )
    providers = ProviderRegistry(
        [
            ProviderDescriptor(id=name, version="1.0", capabilities=[CAPABILITY])
            for name in ("stub", "other")
        ]
    )
    calls = []

    class CountingProvider(StubProvider):
        def execute(self, *args, **kwargs):
            version = kwargs.pop("capability_version", 1)
            calls.append((self.descriptor.id, version))
            return replace(super().execute(*args, **kwargs), version=self.descriptor.version)

    app = create_app(capabilities, providers)
    app.state.executor = CapabilityExecutor(capabilities, providers, CountingProvider)
    app.state.test_calls = calls
    with TestClient(app) as transport:
        yield transport


def invoke(
    client,
    *,
    content=PAYLOAD,
    filename="doc.pdf",
    media_type=PDF,
    fields=None,
    headers=None,
    artifact_id=None,
):
    data = {"provider": "stub", "version": "1", **(fields or {})}
    files = {"file": (filename, content, media_type)}
    if artifact_id is not None:
        files = None
        data["artifact_id"] = artifact_id
    return client.post(ENDPOINT, files=files, data=data, headers={**HEADERS, **(headers or {})})


@pytest.mark.parametrize(
    "change",
    [
        {"content": b"%PDF-different"},
        {"filename": "different.pdf"},
        {"media_type": "application/octet-stream"},
        {"fields": {"parameters": '{"tables":true}'}},
        {"fields": {"language": "en"}},
        {"fields": {"no_cache": "true"}},
        {"fields": {"version": "2"}},
        {"fields": {"provider": "other"}},
        {"artifact_id": "sha256:" + "b" * 64},
    ],
)
def test_changed_execution_request_cannot_join_a_finished_result(client, change):
    first = invoke(client)
    assert first.status_code == 200, first.text
    changed = invoke(client, **change)
    assert changed.status_code == 400, changed.text
    assert changed.json()["code"] == "invalid_input"
    assert len(client.app.state.test_calls) == 1
    retry = invoke(client, headers={"X-Acessilia-Attempt-Id": "attempt-3"})
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert len(client.app.state.test_calls) == 1


def test_parameter_key_order_and_explicit_defaults_join_the_same_request(client):
    first = invoke(client, fields={"parameters": '{"a":1,"b":[true,null]}'})
    second = invoke(
        client,
        fields={
            "parameters": '{ "b": [true, null], "a": 1 }',
            "language": "pt-BR",
            "no_cache": "false",
        },
        headers={"X-Acessilia-Attempt-Id": "attempt-2"},
    )
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(client.app.state.test_calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"content": b"%PDF-new"},
        {"fields": {"provider": "other"}},
        {"fields": {"version": "2"}},
        {"fields": {"parameters": '{"a":1}'}},
    ],
)
def test_changed_request_is_rejected_while_the_owner_runs(client, monkeypatch, change):
    entered, release = threading.Event(), threading.Event()
    original = client.app.state.executor.execute

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.executor, "execute", blocked)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(client.app), base_url="http://test"
        ) as api:
            first = asyncio.create_task(
                api.post(
                    ENDPOINT,
                    headers=HEADERS,
                    files={"file": ("doc.pdf", PAYLOAD, PDF)},
                    data={"provider": "stub", "version": "1"},
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                response = await asyncio.wait_for(
                    api.post(
                        ENDPOINT,
                        headers=HEADERS,
                        files={"file": ("doc.pdf", change.get("content", PAYLOAD), PDF)},
                        data={"provider": "stub", "version": "1", **change.get("fields", {})},
                    ),
                    10,
                )
                assert response.status_code == 400, response.text
                assert not first.done()
            finally:
                release.set()
                result = await first
            assert result.status_code == 200
            assert len(client.app.state.test_calls) == 1

    asyncio.run(run())


def test_identity_waiters_do_not_starve_owner_with_one_worker(client, monkeypatch):
    from acessilia_toolbox.api import rest

    async def run():
        limiter = anyio.to_thread.current_default_thread_limiter()
        old_limit = limiter.total_tokens
        limiter.total_tokens = 1
        paused, resume, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        first_check = True
        joins = 0
        original_check = rest._check_upload_stream
        original_wait = client.app.state.operations.wait_for

        async def check(file):
            nonlocal first_check
            if first_check:
                first_check = False
                paused.set()
                await resume.wait()
            await original_check(file)

        async def wait(operation, *, request_ready=False):
            nonlocal joins
            if request_ready:
                joins += 1
                if joins == 2:
                    waiting.set()
            await original_wait(operation, request_ready=request_ready)

        monkeypatch.setattr(rest, "_check_upload_stream", check)
        monkeypatch.setattr(client.app.state.operations, "wait_for", wait)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(client.app), base_url="http://test"
            ) as api:

                async def post():
                    return await api.post(
                        ENDPOINT,
                        headers=HEADERS,
                        files={"file": ("doc.pdf", PAYLOAD, PDF)},
                        data={"provider": "stub", "version": "1"},
                    )

                owner = asyncio.create_task(post())
                tasks = [owner]
                try:
                    await asyncio.wait_for(paused.wait(), 10)
                    tasks += [asyncio.create_task(post()) for _ in range(2)]
                    await asyncio.wait_for(waiting.wait(), 10)
                finally:
                    resume.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
                assert all(r.status_code == 200 for r in results)
                assert all(r.json() == results[0].json() for r in results)
                assert len(client.app.state.test_calls) == 1
        finally:
            limiter.total_tokens = old_limit

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields",
    [
        {"parameters": "[]"},
        {"parameters": '{"invalid":NaN}'},
        {"version": "99"},
    ],
)
def test_preparation_refusals_release_the_id_and_execution_slot(client, fields):
    response = invoke(client, fields=fields)
    assert response.status_code in {400, 404}
    assert client.app.state.operations.get(HEADERS["X-Acessilia-Operation-Id"]) is None
    retry = invoke(client)
    assert retry.status_code == 200, retry.text
    assert len(client.app.state.test_calls) == 1


def test_invalid_retry_never_mutates_the_retained_owner(client):
    first = invoke(client)
    operation = client.app.state.operations.get(HEADERS["X-Acessilia-Operation-Id"])
    binding = operation.request_fingerprint
    response = invoke(client, fields={"parameters": "[]"})
    assert response.status_code == 400
    assert operation.state == "finished" and operation.request_fingerprint == binding
    assert invoke(client).json() == first.json()


def test_failed_execution_retains_its_identity_and_original_error(client, monkeypatch):
    execute = Mock(side_effect=ProviderTimeoutError("original timeout"))
    monkeypatch.setattr(client.app.state.executor, "execute", execute)
    first = invoke(client)
    assert first.status_code == 504
    assert invoke(client, content=b"other").status_code == 400
    assert invoke(client).json() == first.json()
    execute.assert_called_once()


def test_task_and_authentication_scope_precede_request_matching(client):
    first = invoke(client)
    assert invoke(client, headers={"X-Acessilia-Task-Id": "another-task"}).status_code == 404
    assert invoke(client, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert invoke(client).json() == first.json()
    assert len(client.app.state.test_calls) == 1


def test_changed_provider_configuration_is_not_an_old_operation(client):
    first = invoke(client)
    client.app.state.providers.resolve(CAPABILITY, "stub").config["policy"] = "changed"
    assert invoke(client).status_code == 400
    assert len(client.app.state.test_calls) == 1
    assert first.status_code == 200


def test_default_bindings_match_explicit_selection_but_not_a_new_latest_version(client):
    client.app.state.providers.get("other").capabilities = ["unused.run"]

    def implicit():
        return client.post(
            ENDPOINT, headers=HEADERS, files={"file": ("doc.pdf", PAYLOAD, PDF)}
        )

    first = implicit()
    assert first.status_code == 200
    assert client.app.state.test_calls == [("stub", 2)]
    assert invoke(client, fields={"version": "2"}).json() == first.json()
    client.app.state.capabilities.register(
        CapabilityManifest.model_validate({**MANIFEST, "version": 3})
    )
    assert implicit().status_code == 400
    assert invoke(client, fields={"version": "2"}).json() == first.json()
    assert client.app.state.test_calls == [("stub", 2)]


def test_changed_capability_binding_is_not_an_old_operation(client):
    assert invoke(client).status_code == 200
    client.app.state.capabilities.get(CAPABILITY, 1).execution.cacheable = False
    assert invoke(client).status_code == 400
    assert len(client.app.state.test_calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"content": b"new"},
        {"filename": "new.pdf"},
        {"media_type": "text/plain"},
    ],
)
def test_upload_operation_matches_content_and_metadata(client, monkeypatch, tmp_path, change):
    runner = client.app.state.executor
    runner._store = FilesystemArtifactStore(tmp_path)
    store = Mock(wraps=runner.store_artifact_stream)
    monkeypatch.setattr(runner, "store_artifact_stream", store)
    first = client.post("/v1/artifacts", headers=HEADERS, files={"file": ("doc.pdf", PAYLOAD, PDF)})
    assert first.status_code == 200, first.text
    response = client.post(
        "/v1/artifacts",
        headers=HEADERS,
        files={
            "file": (
                change.get("filename", "doc.pdf"),
                change.get("content", PAYLOAD),
                change.get("media_type", PDF),
            )
        },
    )
    assert response.status_code == 400
    same = client.post("/v1/artifacts", headers=HEADERS, files={"file": ("doc.pdf", PAYLOAD, PDF)})
    assert same.json() == first.json()
    store.assert_called_once()


def test_artifact_retries_match_references_without_redownloading(client, monkeypatch, tmp_path):
    runner = client.app.state.executor
    runner._store = FilesystemArtifactStore(tmp_path)
    upload = client.post(
        "/v1/artifacts",
        headers={"Authorization": HEADERS["Authorization"]},
        files={"file": ("doc.pdf", PAYLOAD, PDF)},
    )
    artifact_id = upload.json()["artifact_id"]
    first = invoke(client, artifact_id=artifact_id)
    assert first.status_code == 200, first.text
    retrieve = Mock(side_effect=ProviderUnavailableError("store unavailable now"))
    monkeypatch.setattr(runner, "retrieve_artifact_to", retrieve)
    same = invoke(client, artifact_id=artifact_id.removeprefix("sha256:"))
    assert same.json() == first.json()
    assert invoke(client, artifact_id="sha256:" + "b" * 64).status_code == 400
    assert invoke(client, artifact_id="invalid").status_code == 400
    retrieve.assert_not_called()
    assert len(client.app.state.test_calls) == 1


def test_artifact_preparation_failure_can_be_retried_after_storage_recovers(
    client, monkeypatch, tmp_path
):
    runner = client.app.state.executor
    runner._store = FilesystemArtifactStore(tmp_path)
    reference = runner.store_artifact(PAYLOAD, media_type=PDF, filename="doc.pdf")
    original = runner.retrieve_artifact_to
    monkeypatch.setattr(
        runner, "retrieve_artifact_to", Mock(side_effect=ProviderUnavailableError("outage"))
    )
    assert invoke(client, artifact_id=reference.artifact_id).status_code == 503
    assert client.app.state.operations.get(HEADERS["X-Acessilia-Operation-Id"]) is None
    monkeypatch.setattr(runner, "retrieve_artifact_to", original)
    assert invoke(client, artifact_id=reference.artifact_id).status_code == 200
    assert len(client.app.state.test_calls) == 1


def test_upload_result_cannot_be_cast_to_execution_with_the_same_capability(client, tmp_path):
    client.app.state.executor._store = FilesystemArtifactStore(tmp_path)
    client.app.state.capabilities.register(
        CapabilityManifest.model_validate({**MANIFEST, "id": "artifact.store"})
    )
    client.app.state.providers.resolve(CAPABILITY, "stub").capabilities.append("artifact.store")
    first = client.post("/v1/artifacts", headers=HEADERS, files={"file": ("doc.pdf", PAYLOAD, PDF)})
    assert first.status_code == 200
    response = client.post(
        "/v1/capabilities/artifact.store:execute",
        headers=HEADERS,
        files={"file": ("doc.pdf", PAYLOAD, PDF)},
        data={"provider": "stub", "version": "1"},
    )
    assert response.status_code == 400
    assert client.app.state.test_calls == []
