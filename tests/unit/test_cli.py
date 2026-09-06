"""Command-line interface behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.documents import FakeDocument

from acessilia_toolbox import cli
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderHealth

CAPABILITIES = """
id: document.structure.extract
version: 1
description: Extract document structure.
input:
  schema: artifact/document@1
  media_types: [application/pdf]
output:
  schema: artifact/structured-document@1
providers:
  - id: docling
""".strip()

PROVIDERS = """
providers:
  - id: docling
    version: "1.32"
    transport: http
    endpoint: http://localhost:5001
    capabilities:
      - document.structure.extract
    config:
      secret_key: SUPERSECRET
""".strip()


class StubProvider:
    def __init__(self, descriptor: Any) -> None:
        self.descriptor = descriptor

    def execute(self, capability_id: str, payload: bytes, **_: Any) -> ExtractionResult:
        timestamp = datetime(2026, 9, 6, tzinfo=UTC)
        return ExtractionResult(
            document=FakeDocument(),
            backend="docling",
            started_at=timestamp,
            completed_at=timestamp,
            duration_ms=3,
            version="1.32.0",
            configuration={},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id, healthy=True, checked_at=datetime.now(UTC)
        )

    def versions(self) -> dict[str, str]:
        return {"provider": "1.32.0"}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "capabilities").mkdir()
    (tmp_path / "capabilities" / "extract.yaml").write_text(CAPABILITIES, encoding="utf-8")
    (tmp_path / "providers-config.yaml").write_text(PROVIDERS, encoding="utf-8")
    (tmp_path / "sample.pdf").write_bytes(b"%PDF-test")
    monkeypatch.setattr(cli, "create_adapter", lambda d: StubProvider(d))
    return tmp_path


def run(workspace: Path, *args: str) -> int:
    return cli.main(
        [
            "--capabilities-dir",
            str(workspace / "capabilities"),
            "--providers-config",
            str(workspace / "providers-config.yaml"),
            *args,
        ]
    )


def test_lists_capabilities(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(workspace, "capabilities") == cli.EXIT_OK

    output = capsys.readouterr().out
    assert "document.structure.extract@1" in output
    assert "providers: docling" in output


def test_lists_capabilities_as_json(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(workspace, "capabilities", "--json") == cli.EXIT_OK

    [manifest] = json.loads(capsys.readouterr().out)
    assert manifest["id"] == "document.structure.extract"


def test_lists_providers_without_leaking_credentials(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(workspace, "providers", "--json") == cli.EXIT_OK

    output = capsys.readouterr().out
    assert "SUPERSECRET" not in output
    assert json.loads(output)[0]["config"]["secret_key"] == "***"


def test_probes_provider_health(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(workspace, "providers", "--health") == cli.EXIT_OK

    assert "status: healthy" in capsys.readouterr().out


def test_executes_and_writes_the_document(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = workspace / "result.json"
    code = run(
        workspace,
        "execute",
        "document.structure.extract",
        str(workspace / "sample.pdf"),
        "-o",
        str(output),
    )

    assert code == cli.EXIT_OK
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["$schema"] == "urn:a11y-devs:schema:processing-manifest:1.1.0"
    assert "via docling" in capsys.readouterr().out


def test_default_output_path_sits_next_to_the_document(workspace: Path) -> None:
    run(workspace, "execute", "document.structure.extract", str(workspace / "sample.pdf"))

    assert (workspace / "sample.structured-document.json").is_file()


def test_provenance_goes_to_stderr(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        workspace,
        "execute",
        "document.structure.extract",
        str(workspace / "sample.pdf"),
        "--provenance",
    )

    provenance = json.loads(capsys.readouterr().err)
    assert provenance["provider"] == "docling"
    assert provenance["capability"] == "document.structure.extract"


def test_missing_document_is_reported(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(workspace, "execute", "document.structure.extract", str(workspace / "no.pdf"))

    assert code == cli.EXIT_USAGE
    assert "file not found" in capsys.readouterr().err


def test_unknown_capability_is_reported(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(workspace, "execute", "speech.synthesize", str(workspace / "sample.pdf"))

    assert code == cli.EXIT_ERROR
    assert "capability_not_found" in capsys.readouterr().err


def test_malformed_parameters_are_reported(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(
        workspace,
        "execute",
        "document.structure.extract",
        str(workspace / "sample.pdf"),
        "--parameters",
        "not-json",
    )

    assert code == cli.EXIT_ERROR
    assert "invalid_input" in capsys.readouterr().err


def test_media_type_is_derived_from_the_extension(workspace: Path) -> None:
    assert cli._media_type(Path("a.pdf")) == "application/pdf"
    assert cli._media_type(Path("a.png")) == "image/png"
    assert cli._media_type(Path("a.unknown")) == "application/octet-stream"
