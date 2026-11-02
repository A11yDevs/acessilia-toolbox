"""Audiveris OMR sidecar server.

Wraps the Audiveris CLI (Java) behind a small HTTP API so the Toolbox can keep
its promise of shipping no ML runtime: the OMR engine lives here, out of
process, exactly like docling-serve and mineru-serve.

Audiveris is a desktop application, so the CLI runs under a virtual X display
(Xvfb) in batch mode. The container starts Xvfb once and every request reuses
that display.

Contract consumed by ``AudiverisProvider``:

- ``GET  /health``  -> 200 once the CLI is usable, 503 before that
- ``GET  /version`` -> ``{"version": ..., "audiveris": ...}``
- ``POST /convert`` -> multipart ``files`` + ``output_format``
                       -> ``{"notation": ..., "format": ..., "confidence": ...}``
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

LOG = logging.getLogger("audiveris_serve")
logging.basicConfig(level=logging.INFO)

AUDIVERIS_BIN = os.environ.get("AUDIVERIS_BIN", "/opt/audiveris/bin/Audiveris")
DISPLAY = os.environ.get("AUDIVERIS_DISPLAY", ":99")
# Audiveris is slow on dense scores; the Toolbox allows 600s per request.
CONVERT_TIMEOUT_SECONDS = int(os.environ.get("AUDIVERIS_TIMEOUT_SECONDS", "540"))

# Audiveris exports MusicXML; MEI is not one of its output formats.
SUPPORTED_FORMATS = ("musicxml",)

state: dict[str, Any] = {
    "xvfb": None,
    "version": None,
    "ready": False,
}


def _run_cli(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run the Audiveris CLI on the virtual display."""
    env = {**os.environ, "DISPLAY": DISPLAY}
    return subprocess.run(
        [AUDIVERIS_BIN, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _detect_version() -> str | None:
    try:
        result = _run_cli(["-version"], timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Could not read the Audiveris version: %s", exc)
        return None
    for line in (result.stdout + result.stderr).splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("- version:"):
            return stripped.split(":", 1)[1].strip()
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Xvfb and confirm the CLI answers before accepting traffic."""
    if shutil.which("Xvfb") is None:
        LOG.error("Xvfb is not installed; Audiveris cannot run headless.")
        yield
        return

    xvfb = subprocess.Popen(
        ["Xvfb", DISPLAY, "-screen", "0", "1280x1024x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    state["xvfb"] = xvfb
    LOG.info("Xvfb started on %s (pid %s)", DISPLAY, xvfb.pid)

    version = _detect_version()
    if version is None:
        LOG.error("Audiveris CLI did not report a version; /health stays 503.")
    else:
        state["version"] = version
        state["ready"] = True
        LOG.info("Audiveris %s ready.", version)

    try:
        yield
    finally:
        if xvfb.poll() is None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                xvfb.kill()
        state.clear()


app = FastAPI(title="audiveris-serve", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    # An unready engine must fail `curl -fsS` with 503 so Compose never marks a
    # sidecar without a working CLI as healthy.
    if not state.get("ready"):
        raise HTTPException(status_code=503, detail="Audiveris CLI is not ready")
    return {"status": "ok", "audiveris": state.get("version")}


@app.get("/version")
def version() -> dict[str, Any]:
    return {
        "version": str(state.get("version") or "unknown"),
        "audiveris": state.get("version"),
        "capability": "music.omr",
    }


def _find_export(output_dir: Path, output_format: str) -> Path | None:
    """Locate the exported score, preferring the requested format."""
    patterns = ("*.mxl", "*.musicxml", "*.xml") if output_format == "musicxml" else ("*.xml",)
    for pattern in patterns:
        candidates = sorted(output_dir.rglob(pattern))
        if candidates:
            return candidates[0]
    return None


def _read_notation(path: Path) -> str:
    """Return MusicXML text from an Audiveris export.

    Audiveris writes a compressed MusicXML container (``.mxl``), which is a ZIP
    holding the score as XML. Reading it as text yields binary noise, so the
    archive is unpacked and the score member returned.
    """
    raw = path.read_bytes()
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".xml") and not name.startswith("META-INF")
            ]
            if not members:
                raise HTTPException(
                    status_code=422,
                    detail=f"Audiveris archive {path.name} carries no MusicXML member",
                )
            return archive.read(sorted(members)[0]).decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _failure_detail(result: subprocess.CompletedProcess[str], output_dir: Path) -> str:
    """Explain why no score was exported, using Audiveris' own log."""
    for log_file in sorted(output_dir.rglob("*.log")):
        for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "StepException" in line or "Error processing stub" in line:
                return f"Audiveris could not transcribe the sheet: {line.strip()}"
    tail = (result.stderr or result.stdout or "").strip().splitlines()
    return "Audiveris produced no score" + (f": {tail[-1]}" if tail else "")


@app.post("/convert")
async def convert(
    files: Annotated[UploadFile, File(description="Sheet music image or PDF")],
    output_format: Annotated[str, Form()] = "musicxml",
) -> dict[str, Any]:
    if not state.get("ready"):
        raise HTTPException(status_code=503, detail="Audiveris CLI is not ready")

    requested = output_format.strip().lower()
    if requested not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported output_format: {output_format}; "
                f"Audiveris exports {', '.join(SUPPORTED_FORMATS)}"
            ),
        )

    payload = await files.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty input payload")

    filename = Path(files.filename or "score.png").name
    with tempfile.TemporaryDirectory(prefix="audiveris-") as tmp:
        workdir = Path(tmp)
        input_path = workdir / filename
        input_path.write_bytes(payload)
        output_dir = workdir / "out"
        output_dir.mkdir()

        try:
            result = _run_cli(
                ["-batch", "-export", "-output", str(output_dir), str(input_path)],
                timeout=CONVERT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Audiveris timed out after {CONVERT_TIMEOUT_SECONDS}s",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail=f"Audiveris could not be executed: {exc}"
            ) from exc

        exported = _find_export(output_dir, requested)
        if exported is None:
            # Audiveris exits 0 even when transcription fails, so the absence of
            # an export is the real signal. Surface its own diagnostics.
            raise HTTPException(status_code=422, detail=_failure_detail(result, output_dir))

        notation = _read_notation(exported)

    return {
        "notation": notation,
        "format": requested,
        "confidence": None,
        "source_filename": filename,
    }
