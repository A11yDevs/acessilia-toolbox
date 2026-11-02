"""veraPDF sidecar that checks a PDF against the PDF/UA validation profiles.

veraPDF is pinned by docker/verapdf-validate.Dockerfile. ``/version`` reports
its release together with this server's version; the adapter feeds both into
the execution cache key, so a veraPDF upgrade re-validates instead of serving
a verdict produced by older rules.

A non-compliant PDF is a successful validation: the report says what failed.
Only a file veraPDF cannot read (not a PDF, damaged, encrypted) is a caller
error.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

SERVER_VERSION = "1.0.0"
VERAPDF = os.environ.get("VERAPDF_BIN", "verapdf")
VALIDATE_TIMEOUT_SECONDS = int(os.environ.get("VERAPDF_TIMEOUT_SECONDS", "240"))
MAX_PDF_BYTES = int(os.environ.get("VERAPDF_MAX_PDF_BYTES", str(200 * 1024 * 1024)))
# veraPDF lists every failed check of a rule; a badly tagged book has thousands.
# The rule summary keeps the true count, the report keeps the first few contexts.
MAX_CHECKS_PER_RULE = int(os.environ.get("VERAPDF_MAX_CHECKS_PER_RULE", "5"))
# Profiles this capability exposes: veraPDF's --flavour value and the prefix of
# the profileName it reports, which picks the matching result from the report.
PROFILES = {
    "ua1": ("ua1", "PDF/UA-1"),
    "ua2": ("ua2", "PDF/UA-2"),
}
UPLOAD_CHUNK_BYTES = 1024 * 1024
STDERR_TAIL_CHARS = 2000

LOG = logging.getLogger("verapdf_validate")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="verapdf-validate")


class ReportError(ValueError):
    """veraPDF ran but produced no usable validation result."""

    def __init__(self, message: str, *, caller_error: bool) -> None:
        super().__init__(message)
        self.caller_error = caller_error


@lru_cache(maxsize=1)
def toolchain() -> dict[str, str]:
    """veraPDF release; computed once per process."""
    try:
        result = subprocess.run(
            [VERAPDF, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"verapdf": ""}
    match = re.search(r"veraPDF\s+(\S+)", result.stdout)
    return {"verapdf": match.group(1) if match else ""}


@app.get("/health")
def health() -> dict[str, Any]:
    if shutil.which(VERAPDF) is None:
        raise HTTPException(status_code=503, detail=f"missing: {VERAPDF}")
    return {"status": "ok", "version": SERVER_VERSION}


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": SERVER_VERSION, **toolchain(), "capability": "pdf.validate.pdf_ua"}


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _failure(rule: dict[str, Any]) -> dict[str, Any]:
    checks = [
        {"context": str(check.get("context", "")), "message": str(check.get("errorMessage", ""))}
        for check in rule.get("checks") or []
        if isinstance(check, dict) and check.get("status") == "failed"
    ]
    return {
        "specification": str(rule.get("specification", "")),
        "clause": str(rule.get("clause", "")),
        "test_number": _int(rule.get("testNumber")),
        "description": str(rule.get("description", "")),
        "object": str(rule.get("object", "")),
        "test": str(rule.get("test", "")),
        "tags": [str(tag) for tag in rule.get("tags") or []],
        "failed_checks": _int(rule.get("failedChecks")),
        "checks": checks[:MAX_CHECKS_PER_RULE],
    }


def normalize_report(raw: dict[str, Any], profile: str) -> dict[str, Any]:
    """Reduce veraPDF's JSON report for one file to the capability's contract.

    The report names the file by its temporary path, so nothing from
    ``itemDetails`` is carried over.
    """
    try:
        job = raw["report"]["jobs"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReportError("veraPDF report has no job", caller_error=False) from exc

    results = job.get("validationResult")
    # Older releases emit an object, 1.26+ a list with one entry per profile.
    if isinstance(results, dict):
        results = [results]
    if not results:
        exception = job.get("taskException") or {}
        kind = str(exception.get("type", "")).upper()
        message = str(exception.get("exceptionMessage") or exception.get("exception") or "")
        # PARSE and ENCRYPTION describe the uploaded file; anything else is ours.
        caller_error = kind in {"PARSE", "ENCRYPTION"}
        raise ReportError(
            f"veraPDF could not validate the file ({kind or 'unknown'}): {message}",
            caller_error=caller_error,
        )

    # 1.30 returns exactly one result per --flavour. Match it by name anyway, so
    # a release that adds a profile can never pass another profile's numbers off
    # as the requested one.
    prefix = PROFILES[profile][1]
    matching = [
        item
        for item in results
        if isinstance(item, dict) and str(item.get("profileName", "")).startswith(prefix)
    ]
    if len(matching) != 1:
        names = [item.get("profileName") for item in results if isinstance(item, dict)]
        raise ReportError(
            f"expected one {prefix} result in the veraPDF report, got {names}",
            caller_error=False,
        )
    result = matching[0]
    details = result.get("details") or {}
    rules = details.get("ruleSummaries") or []
    return {
        "compliant": bool(result.get("compliant")),
        "profile": profile,
        "profile_name": str(result.get("profileName", "")),
        "statement": str(result.get("statement", "")),
        "summary": {
            "passed_rules": _int(details.get("passedRules")),
            "failed_rules": _int(details.get("failedRules")),
            "passed_checks": _int(details.get("passedChecks")),
            "failed_checks": _int(details.get("failedChecks")),
        },
        "failures": [
            _failure(rule)
            for rule in rules
            if isinstance(rule, dict) and str(rule.get("status", "")).lower() == "failed"
        ],
    }


def run_verapdf(pdf: Path, profile: str) -> dict[str, Any]:
    """Validate one file and return the normalized report."""
    command = [VERAPDF, "--format", "json", "--flavour", PROFILES[profile][0], str(pdf)]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            cwd=pdf.parent,
            timeout=VALIDATE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504, detail=f"validation exceeded {VALIDATE_TIMEOUT_SECONDS}s"
        ) from exc
    # Exit codes: 0 compliant, 1 not compliant, 7 unreadable file; the JSON
    # report is what decides, the code only explains a missing report.
    try:
        raw = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        LOG.warning("verapdf failed (%s): %s", result.returncode, stderr[-STDERR_TAIL_CHARS:])
        raise HTTPException(
            status_code=500,
            detail=f"verapdf exited {result.returncode}: {stderr[-STDERR_TAIL_CHARS:]}",
        ) from exc
    try:
        return normalize_report(raw, profile)
    except ReportError as exc:
        raise HTTPException(status_code=422 if exc.caller_error else 500, detail=str(exc)) from exc


@app.post("/validate")
def validate(
    file: Annotated[UploadFile, File(description="PDF to validate")],
    profile: Annotated[str, Form(description="Validation profile: ua1 or ua2")] = "ua1",
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown profile {profile!r}; expected one of {sorted(PROFILES)}",
        )
    with tempfile.TemporaryDirectory(prefix="verapdf-") as tmp:
        pdf = Path(tmp) / "document.pdf"
        _save_upload(file, pdf)
        if shutil.which(VERAPDF) is None:
            raise HTTPException(status_code=503, detail=f"missing: {VERAPDF}")
        return run_verapdf(pdf, profile)


def _save_upload(file: UploadFile, destination: Path) -> None:
    """Copy the upload to disk in chunks, so an oversized body never sits in memory."""
    total = 0
    head = b""
    with destination.open("wb") as out:
        while chunk := file.file.read(UPLOAD_CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                raise HTTPException(status_code=413, detail=f"PDF exceeds {MAX_PDF_BYTES} bytes")
            if len(head) < 1024:
                head += chunk[: 1024 - len(head)]
            out.write(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="empty PDF payload")
    # PDF readers accept the header anywhere in the first kilobyte.
    if b"%PDF-" not in head:
        raise HTTPException(status_code=400, detail="payload is not a PDF (no %PDF- header)")
