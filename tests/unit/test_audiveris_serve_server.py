"""audiveris-serve sidecar: health contract and MusicXML extraction."""

from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

SERVER_PATH = (
    Path(__file__).resolve().parents[2] / "docker" / "audiveris-serve" / "server.py"
)

SCORE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<score-partwise version="4.0.3"><part-list/></score-partwise>'
)


@pytest.fixture(scope="module")
def server() -> Any:
    spec = importlib.util.spec_from_file_location("audiveris_serve_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def ready(server: Any) -> Any:
    """Most tests exercise a sidecar whose CLI answered at boot."""
    previous = dict(server.state)
    server.state.update({"ready": True, "version": "5.11.0"})
    yield
    server.state.clear()
    server.state.update(previous)


def _mxl_bytes(score: str = SCORE_XML) -> bytes:
    """Build a compressed MusicXML container, the way Audiveris writes it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("score.xml", score)
    return buffer.getvalue()


class TestHealth:
    def test_health_is_503_until_the_cli_is_ready(self, server: Any) -> None:
        server.state["ready"] = False
        with pytest.raises(HTTPException) as excinfo:
            server.health()
        assert excinfo.value.status_code == 503

    def test_health_reports_the_audiveris_version(self, server: Any) -> None:
        assert server.health() == {"status": "ok", "audiveris": "5.11.0"}

    def test_version_exposes_the_engine(self, server: Any) -> None:
        payload = server.version()
        assert payload["version"] == "5.11.0"
        assert payload["audiveris"] == "5.11.0"
        assert payload["capability"] == "music.omr"


class TestReadNotation:
    def test_compressed_musicxml_is_unpacked(self, server: Any, tmp_path: Path) -> None:
        """Audiveris writes .mxl, a ZIP; reading it as text yields binary noise."""
        export = tmp_path / "score.mxl"
        export.write_bytes(_mxl_bytes())

        notation = server._read_notation(export)

        assert notation.startswith("<?xml")
        assert "score-partwise" in notation

    def test_plain_xml_is_returned_as_is(self, server: Any, tmp_path: Path) -> None:
        export = tmp_path / "score.xml"
        export.write_text(SCORE_XML, encoding="utf-8")

        assert server._read_notation(export) == SCORE_XML

    def test_archive_without_a_score_member_is_rejected(
        self, server: Any, tmp_path: Path
    ) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("META-INF/container.xml", "<container/>")
        export = tmp_path / "score.mxl"
        export.write_bytes(buffer.getvalue())

        with pytest.raises(HTTPException) as excinfo:
            server._read_notation(export)

        assert excinfo.value.status_code == 422


class TestFindExport:
    def test_prefers_the_compressed_container(self, server: Any, tmp_path: Path) -> None:
        (tmp_path / "score.omr").write_bytes(b"book")
        (tmp_path / "score.mxl").write_bytes(_mxl_bytes())

        found = server._find_export(tmp_path, "musicxml")

        assert found is not None and found.name == "score.mxl"

    def test_returns_none_when_nothing_was_exported(self, server: Any, tmp_path: Path) -> None:
        (tmp_path / "score.omr").write_bytes(b"book")

        assert server._find_export(tmp_path, "musicxml") is None


class TestFailureDetail:
    def test_reports_the_step_exception_from_the_log(
        self, server: Any, tmp_path: Path
    ) -> None:
        """Audiveris exits 0 on failure, so its log carries the real reason."""
        (tmp_path / "score-20260101T000000.log").write_text(
            "INFO  | 1 sheet in score.png\n"
            "WARN  | Error processing stub org.audiveris.omr.step.StepException: "
            "No system found\n",
            encoding="utf-8",
        )
        result = type("R", (), {"stdout": "", "stderr": ""})()

        detail = server._failure_detail(result, tmp_path)

        assert "No system found" in detail
