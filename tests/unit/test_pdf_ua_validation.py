"""pdf.validate.pdf_ua: the verapdf adapter and its sidecar's report normalization."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.verapdf import VeraPdfProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = PROJECT_ROOT / "docker" / "verapdf-validate" / "server.py"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "verapdf"
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "pdf-validation-report@1.json"
CAPABILITY = "pdf.validate.pdf_ua"
PDF_BYTES = b"%PDF-1.7\n% fake\n%%EOF\n"
VERSIONS = {"version": "1.0.0", "verapdf": "1.30.2", "capability": CAPABILITY}
REPORT = {
    "compliant": False,
    "profile": "ua1",
    "profile_name": "PDF/UA-1 validation profile",
    "statement": "PDF file is not compliant with Validation Profile requirements.",
    "summary": {"passed_rules": 100, "failed_rules": 1, "passed_checks": 29, "failed_checks": 1},
    "failures": [
        {
            "specification": "ISO 14289-1:2014",
            "clause": "7.1",
            "test_number": 11,
            "description": "StructTreeRoot required",
            "object": "PDDocument",
            "test": "containsStructTreeRoot == true",
            "tags": ["structure"],
            "failed_checks": 1,
            "checks": [{"context": "root/document[0]", "message": "missing"}],
        }
    ],
}


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value


def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": "verapdf",
            "version": "1.0",
            "transport": "http",
            "endpoint": "http://verapdf-validate:5008",
            "timeout_seconds": 5.0,
            "capabilities": [CAPABILITY],
            "media_types": ["application/pdf"],
        }
    )


def sidecar(
    validate: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    versions: dict[str, str] | None = None,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json=versions or VERSIONS)
        if request.url.path == "/validate":
            if validate is not None:
                return validate(request)
            return httpx.Response(200, json=REPORT)
        return httpx.Response(200, json={"status": "ok", "version": "1.0.0"})

    return handler, calls


def provider_with(handler: Callable[[httpx.Request], httpx.Response]) -> VeraPdfProvider:
    adapter = VeraPdfProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def validate_pdf(adapter: VeraPdfProvider, **parameters: Any) -> Any:
    return adapter.execute(
        CAPABILITY,
        PDF_BYTES,
        filename="relatorio.pdf",
        media_type="application/pdf",
        parameters=parameters,
    )


def executor_with(adapter: VeraPdfProvider, cache: MemoryCache) -> CapabilityExecutor:
    return CapabilityExecutor(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"),
        ProviderRegistry([adapter.descriptor]),
        lambda _: adapter,
        cache=cache,
    )


def run(executor: CapabilityExecutor) -> Any:
    return executor.execute(
        CAPABILITY, PDF_BYTES, filename="relatorio.pdf", media_type="application/pdf"
    )


# ── Adapter ─────────────────────────────────────────────────────────────


def test_factory_creates_the_adapter() -> None:
    assert isinstance(create_adapter(descriptor()), VeraPdfProvider)


def test_adapter_returns_the_report_with_the_validator_identity() -> None:
    handler, calls = sidecar()
    result = validate_pdf(provider_with(handler), profile="ua2")

    assert result.outputs == ()
    assert result.document["compliant"] is False
    assert result.document["failures"][0]["clause"] == "7.1"
    assert result.document["validator"] == {
        "name": "veraPDF",
        "version": "1.0.0",
        "verapdf": "1.30.2",
    }
    validate_call = next(call for call in calls if call.url.path == "/validate")
    assert b'name="profile"\r\n\r\nua2' in validate_call.content


def test_report_matches_the_published_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    handler, _ = sidecar()
    document = validate_pdf(provider_with(handler)).document
    jsonschema.validate(document, json.loads(SCHEMA_PATH.read_text()))


def test_unknown_profile_is_rejected_before_calling_the_sidecar() -> None:
    handler, calls = sidecar()
    with pytest.raises(InvalidInputError, match="profile"):
        validate_pdf(provider_with(handler), profile="1b")
    assert not calls


def test_versions_cover_the_verapdf_release() -> None:
    handler, _ = sidecar()
    versions = provider_with(handler).versions()
    assert versions == {"provider": "1.0.0", "version": "1.0.0", "verapdf": "1.30.2"}


@pytest.mark.parametrize("status", [400, 413, 422])
def test_rejected_pdf_is_an_input_error(status: int) -> None:
    handler, _ = sidecar(lambda _: httpx.Response(status, json={"detail": "not a PDF"}))
    with pytest.raises(InvalidInputError, match="not a PDF"):
        validate_pdf(provider_with(handler))


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (504, ProviderTimeoutError),
        (503, ProviderUnavailableError),
        (500, ProviderExecutionError),
    ],
)
def test_sidecar_failures_map_to_provider_errors(status: int, error: type[Exception]) -> None:
    handler, _ = sidecar(lambda _: httpx.Response(status, json={"detail": "boom"}))
    with pytest.raises(error):
        validate_pdf(provider_with(handler))


def test_incomplete_report_is_an_execution_error() -> None:
    handler, _ = sidecar(lambda _: httpx.Response(200, json={"compliant": True}))
    with pytest.raises(ProviderExecutionError, match="lacks"):
        validate_pdf(provider_with(handler))


def test_unreachable_sidecar_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderUnavailableError):
        validate_pdf(provider_with(handler))


# ── Executor ────────────────────────────────────────────────────────────


def test_capability_runs_through_the_executor_and_caches_the_report() -> None:
    handler, calls = sidecar()
    cache = MemoryCache()
    executor = executor_with(provider_with(handler), cache)

    first = run(executor)
    second = run(executor)

    assert first.document["compliant"] is False
    assert not first.provenance.cache_hit
    assert second.provenance.cache_hit
    assert sum(call.url.path == "/validate" for call in calls) == 1


def test_verapdf_upgrade_changes_the_cache_key() -> None:
    cache = MemoryCache()
    handler, _ = sidecar()
    before = run(executor_with(provider_with(handler), cache))
    upgraded, calls = sidecar(versions={**VERSIONS, "verapdf": "1.32.0"})
    after = run(executor_with(provider_with(upgraded), cache))

    assert before.provenance.cache_key != after.provenance.cache_key
    assert not after.provenance.cache_hit
    assert any(call.url.path == "/validate" for call in calls)


# ── Sidecar server ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def server() -> Any:
    spec = importlib.util.spec_from_file_location("verapdf_validate_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_normalizes_a_compliant_verapdf_report(server: Any) -> None:
    report = server.normalize_report(fixture("compliant"), "ua1")
    assert report["compliant"] is True
    assert report["profile_name"] == "PDF/UA-1 validation profile"
    assert report["summary"] == {
        "passed_rules": 106,
        "failed_rules": 0,
        "passed_checks": 235,
        "failed_checks": 0,
    }
    assert report["failures"] == []


def test_normalizes_a_non_compliant_verapdf_report(server: Any) -> None:
    report = server.normalize_report(fixture("non-compliant"), "ua1")
    assert report["compliant"] is False
    assert report["summary"]["failed_rules"] == len(report["failures"]) == 6
    structure = next(
        f for f in report["failures"] if f["clause"] == "7.1" and f["test_number"] == 11
    )
    assert structure["specification"] == "ISO 14289-1:2014"
    assert structure["checks"] == [
        {
            "context": "root/document[0]",
            "message": "StructTreeRoot entry is not present in the document catalog",
        }
    ]
    # The temporary path veraPDF reports must not leak into the result.
    assert "document.pdf" not in json.dumps(report)


def test_caps_the_listed_checks_but_keeps_the_true_count(server: Any) -> None:
    raw = fixture("non-compliant")
    rule = raw["report"]["jobs"][0]["validationResult"][0]["details"]["ruleSummaries"][0]
    rule["checks"] = [dict(rule["checks"][0], context=f"page[{i}]") for i in range(50)]
    rule["failedChecks"] = 50

    failure = server.normalize_report(raw, "ua1")["failures"][0]
    assert failure["failed_checks"] == 50
    assert len(failure["checks"]) == server.MAX_CHECKS_PER_RULE


def test_accepts_the_pre_1_26_single_result_object(server: Any) -> None:
    raw = fixture("compliant")
    job = raw["report"]["jobs"][0]
    job["validationResult"] = job["validationResult"][0]
    assert server.normalize_report(raw, "ua1")["compliant"] is True


def test_unparsable_pdf_is_a_caller_error(server: Any) -> None:
    with pytest.raises(server.ReportError, match="PARSE") as excinfo:
        server.normalize_report(fixture("parse-error"), "ua1")
    assert excinfo.value.caller_error


def test_report_without_jobs_is_a_server_error(server: Any) -> None:
    with pytest.raises(server.ReportError) as excinfo:
        server.normalize_report({"report": {"jobs": []}}, "ua1")
    assert not excinfo.value.caller_error


@pytest.fixture
def client(server: Any) -> TestClient:
    return TestClient(server.app)


def post_pdf(client: TestClient, body: bytes, **data: str) -> httpx.Response:
    return client.post("/validate", files={"file": ("a.pdf", body, "application/pdf")}, data=data)


@pytest.mark.parametrize(
    ("body", "data"),
    [
        (b"", {}),
        (b"<html>not a pdf</html>", {}),
        (PDF_BYTES, {"profile": "1b"}),
    ],
    ids=["empty", "no-pdf-header", "unknown-profile"],
)
def test_server_rejects_bad_requests(client: TestClient, body: bytes, data: dict[str, str]) -> None:
    assert post_pdf(client, body, **data).status_code == 400


def test_server_maps_a_parse_failure_to_422(
    client: TestClient, server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        assert command[1:5] == ["--format", "json", "--flavour", "ua1"]
        stdout = (FIXTURES / "parse-error.json").read_bytes()
        return subprocess.CompletedProcess(command, 7, stdout=stdout, stderr=b"")

    monkeypatch.setattr(server.shutil, "which", lambda _: "/usr/bin/verapdf")
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    response = post_pdf(client, PDF_BYTES)
    assert response.status_code == 422
    assert "PARSE" in response.json()["detail"]


def test_server_maps_a_missing_report_to_500(
    client: TestClient, server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"bad arguments")

    monkeypatch.setattr(server.shutil, "which", lambda _: "/usr/bin/verapdf")
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    response = post_pdf(client, PDF_BYTES)
    assert response.status_code == 500
    assert "bad arguments" in response.json()["detail"]


def test_server_reports_503_without_verapdf(
    client: TestClient, server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server.shutil, "which", lambda _: None)
    assert client.get("/health").status_code == 503
    assert post_pdf(client, PDF_BYTES).status_code == 503
