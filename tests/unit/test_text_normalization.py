"""Bounded normalization through its published contract and real executor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import jsonschema
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.artifact import NullCache
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import (
    InvalidInputError,
    OperationCancelledError,
    ProviderExecutionError,
    ProviderTimeoutError,
    UnsupportedMediaTypeError,
    UploadTooLargeError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes, fingerprint_parameters
from acessilia_toolbox.core.operations import Operation
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.operations.text_normalization import (
    MAX_PAYLOAD_BYTES,
    MAX_TARGETS,
    NormalizationRequest,
    NormalizationResult,
    TextTarget,
    normalize_text,
)
from acessilia_toolbox.providers import create_adapter, docstruct_text
from acessilia_toolbox.providers.docstruct_text import CAPABILITY, DocstructTextProvider

ROOT = Path(__file__).resolve().parents[2]


def descriptor() -> ProviderDescriptor:
    return ProviderRegistry.from_file(ROOT / "providers-config.yaml").resolve(
        CAPABILITY, "docstruct-text"
    )


def payload(text: str | None = "ﬁnal  text") -> bytes:
    return json.dumps({"targets": [{"id": "b1", "text": text}]}, ensure_ascii=False).encode()


def execute(data: bytes, **kwargs):
    return DocstructTextProvider(descriptor()).execute(
        CAPABILITY, data, filename="batch.json", media_type="application/json", **kwargs
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ﬁ ﬂ ﬀ ﬃ ﬄ ﬅ ﬆ", "fi fl ff ffi ffl ft st"),
        ("co\u00adoperate\u00a0 now", "cooperate now"),
        ("inter-\nnational", "international"),
        ("a-\nb-\nc", "ab-\nc"),  # Preserve one pass, not an invented fixed point.
        ("a   b\t\tc", "a b c"),
        ("  $$ a  b $$", "  $$ a  b $$"),
        ("<table>ﬁ  a</table>", "<table>ﬁ  a</table>"),
        ("\\[ a  b \\]", "\\[ a  b \\]"),
        ("| a |  b |", "| a |  b |"),
        ("well-known\n", "well-known\n"),
        ("😀 café", "😀 café"),
        (" ", " "),
        ("", ""),
        (None, None),
    ],
)
def test_one_pass_matches_the_legacy_transformation(source, expected):
    result = execute(payload(source)).document
    [item] = result["results"]
    assert item == {
        "id": "b1",
        "text": expected,
        "processed": bool(source),
        "changed": expected != source,
        "input_fingerprint": fingerprint_parameters({"id": "b1", "text": source}),
    }
    assert result["processed_count"] == int(bool(source))
    assert result["changed_count"] == int(expected != source)
    jsonschema.validate(
        result, json.loads((ROOT / "schemas/text-normalization-result@1.json").read_text())
    )


def test_a_batch_keeps_order_empty_values_and_duplicate_texts_without_mutating_input():
    request = NormalizationRequest.model_validate(
        {
            "targets": [
                {"id": "second", "text": "ﬁ"},
                {"id": "first", "text": "ﬁ"},
                {"id": "empty", "text": ""},
                {"id": "null", "text": None},
                {"id": "noop", "text": "already clean"},
            ]
        }
    )
    before = request.model_dump()
    result = normalize_text(request)
    assert request.model_dump() == before
    assert [r.id for r in result.results] == ["second", "first", "empty", "null", "noop"]
    assert [r.text for r in result.results] == ["fi", "fi", "", None, "already clean"]
    assert (result.processed_count, result.changed_count) == (3, 2)


def test_an_empty_batch_returns_observations_without_deciding_workflow_success():
    assert execute(b'{"targets":[]}').document == {
        "results": [],
        "processed_count": 0,
        "changed_count": 0,
    }


def test_validated_collections_cannot_be_mutated_or_alias_the_input_list():
    data = {"targets": [{"id": "a", "text": "ﬁ"}]}
    request = NormalizationRequest.model_validate(data)
    result = normalize_text(request)
    data["targets"].append({"id": "a", "text": "duplicate"})
    assert len(request.targets) == len(result.results) == 1
    for collection in (request.targets, result.results):
        with pytest.raises(AttributeError):
            collection.append(collection[0])
        with pytest.raises(TypeError):
            collection[0] = collection[0]
    with pytest.raises(ValidationError):
        request.targets[0].text = "changed"
    with pytest.raises(ValidationError):
        result.processed_count = 0
    assert request.model_dump(mode="json")["targets"] == [{"id": "a", "text": "ﬁ"}]
    wire = result.model_dump(mode="json")
    assert isinstance(wire["results"], list)
    assert NormalizationResult.model_validate_json(result.model_dump_json()) == result


def test_only_input_targets_expose_input_fingerprinting():
    target = TextTarget(id="a", text="ﬁ")
    request = NormalizationRequest.model_validate({"targets": [target]})
    observation = normalize_text(request).results[0]
    assert observation.input_fingerprint == target.fingerprint()
    assert not hasattr(observation, "fingerprint")


@pytest.mark.parametrize(
    "case",
    json.loads((ROOT / "tests/fixtures/text-normalization-legacy.json").read_text())["cases"],
    ids=lambda case: case["name"],
)
def test_observations_preserve_the_recorded_core_handler_outcome(case):
    targets = [{"id": item["id"], "text": item["text"]} for item in case["input"]]
    result = execute(json.dumps({"targets": targets}, ensure_ascii=False).encode()).document
    applied = []
    for before, observation in zip(case["input"], result["results"], strict=True):
        assert observation["id"] == before["id"]
        metadata = dict(before["metadata"])
        if observation["changed"]:
            metadata["norm_fixed"] = True
        applied.append({"id": observation["id"], "text": observation["text"], "metadata": metadata})
    assert applied == case["output"]
    assert bool(result["processed_count"]) is case["success"] is case["validated"]


@pytest.mark.parametrize(
    "data",
    [
        b"{}",
        b"[]",
        b'{"targets":null}',
        b'{"targets":',
        b"\xff",
        b'{"targets":[],"targets":[]}',
        b'{"targets":[{"id":"a","id":"b","text":"x"}]}',
        b'{"targets":[{"id":"a","text":NaN}]}',
        b'{"targets":[{"id":"a","text":Infinity}]}',
        b'{"targets":[{"id":"a","text":1e999}]}',
        b'{"targets":[{"id":"a","text":true}]}',
        b'{"targets":[{"id":1,"text":"x"}]}',
        b'{"targets":[{"id":"","text":"x"}]}',
        b'{"targets":[{"id":"a"}]}',
        b'{"targets":[{"id":"a","text":"x","goal":true}]}',
        b'{"targets":[{"id":"a","text":"x"},{"id":"a","text":"y"}]}',
        b'{"targets":[{"id":"a","text":"\\ud800"}]}',
        b'{"targets":[{"id":"\\ud800","text":"x"}]}',
        b'{"targets":[],"obligations":[]}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_invalid_requests_fail_before_the_algorithm(monkeypatch, data):
    algorithm = Mock()
    monkeypatch.setattr(docstruct_text, "normalize_text", algorithm)
    with pytest.raises(InvalidInputError):
        execute(data)
    algorithm.assert_not_called()


def test_item_and_byte_limits_are_enforced():
    targets = [{"id": str(i), "text": "x"} for i in range(MAX_TARGETS)]
    assert (
        execute(json.dumps({"targets": targets}).encode()).document["processed_count"]
        == MAX_TARGETS
    )
    targets.append({"id": "overflow", "text": "x"})
    with pytest.raises(InvalidInputError):
        execute(json.dumps({"targets": targets}).encode())
    with pytest.raises(InvalidInputError):
        execute(json.dumps({"targets": [{"id": "x" * 257, "text": "x"}]}).encode())
    with pytest.raises(UploadTooLargeError):
        execute(payload("é" * MAX_PAYLOAD_BYTES))


def test_exact_byte_limit_is_accepted():
    data = payload("x")
    data += b" " * (MAX_PAYLOAD_BYTES - len(data))
    assert execute(data).document["processed_count"] == 1


def test_parameters_and_unsupported_capability_versions_are_explicit_errors():
    with pytest.raises(InvalidInputError):
        execute(payload(), parameters={"normalize_again": True})
    with pytest.raises(ProviderExecutionError):
        execute(payload(), capability_version=2)
    provider = DocstructTextProvider(descriptor())
    with pytest.raises(ProviderExecutionError):
        provider.execute(
            "text.postprocess", payload(), filename="x.json", media_type="application/json"
        )
    with pytest.raises(UnsupportedMediaTypeError):
        provider.execute(CAPABILITY, payload(), filename="x.txt", media_type="text/plain")


def test_algorithm_failure_and_expired_budget_publish_no_partial_result(monkeypatch):
    original = docstruct_text.normalize_text
    monkeypatch.setattr(docstruct_text, "normalize_text", Mock(side_effect=RuntimeError("boom")))
    with pytest.raises(ProviderExecutionError):
        execute(payload())
    monkeypatch.setattr(docstruct_text, "normalize_text", original)
    clock = iter([0.0, 6.0])
    monkeypatch.setattr(docstruct_text, "perf_counter", lambda: next(clock))
    with pytest.raises(ProviderTimeoutError):
        execute(payload())


@pytest.mark.parametrize(
    ("configured", "elapsed", "times_out"),
    [(1.0, 0.5, False), (1.0, 1.0, False), (1.0, 1.1, True), (10.0, 4.9, False), (10.0, 5.1, True)],
)
def test_configured_timeout_and_cap_are_respected(monkeypatch, configured, elapsed, times_out):
    provider = DocstructTextProvider(
        descriptor().model_copy(update={"timeout_seconds": configured})
    )
    clock = iter([10.0, 10.0 + elapsed])
    monkeypatch.setattr(docstruct_text, "perf_counter", lambda: next(clock))
    if times_out:
        with pytest.raises(ProviderTimeoutError):
            provider.execute(
                CAPABILITY, payload(), filename="batch.json", media_type="application/json"
            )
    else:
        result = provider.execute(
            CAPABILITY, payload(), filename="batch.json", media_type="application/json"
        )
        assert result.document["results"][0]["text"] == "final text"


def executor(*, provider=None, **kwargs):
    from acessilia_toolbox.core.executor import CapabilityExecutor

    return CapabilityExecutor(
        CapabilityRegistry.from_directory(ROOT / "capabilities"),
        ProviderRegistry([provider or descriptor()]),
        create_adapter,
        **kwargs,
    )


def test_executor_timeout_does_not_publish_and_retry_uses_original_input(monkeypatch, tmp_path):
    from acessilia_toolbox.providers.storage import FilesystemArtifactStore

    original = "a-\nb-\nc"
    request_bytes = payload(original)
    clock = iter([0.0, 1.5, 2.0, 2.5])
    monkeypatch.setattr(docstruct_text, "perf_counter", lambda: next(clock))
    algorithm = Mock(wraps=docstruct_text.normalize_text)
    monkeypatch.setattr(docstruct_text, "normalize_text", algorithm)
    cache = Mock(spec=NullCache)
    cache.get.return_value = None
    store = Mock(spec=FilesystemArtifactStore, wraps=FilesystemArtifactStore(tmp_path))
    runtime = executor(
        provider=descriptor().model_copy(update={"timeout_seconds": 1.0}),
        store=store,
        cache=cache,
    )
    with pytest.raises(ProviderTimeoutError):
        runtime.execute(
            CAPABILITY, request_bytes, filename="batch.json", media_type="application/json"
        )
    store.put.assert_not_called()
    cache.put.assert_not_called()
    assert list(tmp_path.rglob("*.json")) == []
    result = runtime.execute(
        CAPABILITY, request_bytes, filename="batch.json", media_type="application/json"
    )
    assert result.document["results"][0]["text"] == "ab-\nc"
    assert [call.args[0].targets[0].text for call in algorithm.call_args_list] == [
        original,
        original,
    ]
    assert result.document["results"][0]["input_fingerprint"] == fingerprint_parameters(
        {"id": "b1", "text": original}
    )
    store.put.assert_called_once()
    cache.put.assert_called_once()


def test_provenance_and_cache_bind_input_versions_and_repeat_the_same_observations():
    from tests.unit.test_executor import DictCache

    runtime = executor(cache=DictCache())
    first = runtime.execute(
        CAPABILITY,
        payload(),
        filename="batch.json",
        media_type="application/json",
        capability_version=1,
    )
    repeated = runtime.execute(
        CAPABILITY,
        payload(),
        filename="batch.json",
        media_type="application/json",
        capability_version=1,
    )
    assert repeated.document == first.document
    assert repeated.provenance.cache_hit
    assert first.provenance.input_fingerprints == [fingerprint_bytes(payload())]
    assert first.provenance.model_versions["docstruct"] == "0.5.8"
    assert first.provenance.model_versions["runtime"]
    assert first.provenance.provider_version == "1.0.0"
    changed = runtime.execute(
        CAPABILITY, payload("new"), filename="batch.json", media_type="application/json"
    )
    assert changed.provenance.cache_key != first.provenance.cache_key
    assert (
        changed.document["results"][0]["input_fingerprint"]
        != first.document["results"][0]["input_fingerprint"]
    )


def test_cancellation_after_computation_prevents_storage_and_cache(monkeypatch):
    operation = Operation("normalize-op", "task", None, CAPABILITY)
    original = docstruct_text.normalize_text

    def cancel_after(request):
        result = original(request)
        operation.cancel_event.set()
        return result

    monkeypatch.setattr(docstruct_text, "normalize_text", cancel_after)
    cache, store = Mock(spec=NullCache), Mock()
    cache.get.return_value = None
    with pytest.raises(OperationCancelledError):
        executor(cache=cache, store=store).execute(
            CAPABILITY,
            payload(),
            filename="batch.json",
            media_type="application/json",
            operation=operation,
        )
    store.put.assert_not_called()
    cache.put.assert_not_called()


def test_schemas_are_generated_from_the_operational_dtos():
    script = ROOT / "scripts/export_text_normalization_schemas.py"
    spec = importlib.util.spec_from_file_location("export_normalization", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, content in module.outputs().items():
        assert path.read_text(encoding="utf-8") == content
    invalid = {"results": [], "processed_count": 1, "changed_count": 0}
    with pytest.raises(ValidationError):
        NormalizationResult.model_validate(invalid)


def test_authenticated_rest_uses_real_dispatch_and_returns_typed_validation_errors(monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "normalization-test-key")
    with TestClient(
        create_app(
            CapabilityRegistry.from_directory(ROOT / "capabilities"),
            ProviderRegistry([descriptor()]),
        )
    ) as client:
        endpoint = f"/v1/capabilities/{CAPABILITY}:execute"
        files = {"file": ("batch.json", payload(), "application/json")}
        assert client.post(endpoint, files=files).status_code == 401
        headers = {"Authorization": "Bearer normalization-test-key"}
        response = client.post(
            endpoint,
            files=files,
            headers=headers,
            data={"version": "1", "provider": "docstruct-text"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["document"]["results"][0]["text"] == "final text"
        assert response.json()["capability"] == f"{CAPABILITY}@1"
        response = client.post(
            endpoint, headers=headers, files={"file": ("bad.json", b"{}", "application/json")}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_input"
