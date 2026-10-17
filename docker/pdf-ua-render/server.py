"""Pandoc + TeX Live sidecar that renders a Pandoc JSON AST into tagged PDF/UA.

The toolchain lives in this image only, pinned by docker/pdf-ua-render.Dockerfile,
so every consumer gets the same LaTeX kernel, tagging packages and template.
``/version`` reports those versions together with the template digest; the
adapter feeds them into the execution cache key, so upgrading any of them
invalidates cached PDFs instead of serving stale ones.

The AST is untrusted input. Raw TeX blocks are rejected, so every string
reaches LaTeX escaped by Pandoc; on top of that, shell escape is off and TeX
may only write below its working directory.
"""

from __future__ import annotations

import hashlib
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
from fastapi.responses import Response

SERVER_VERSION = "1.0.0"
TEMPLATE_PATH = Path(
    os.environ.get("PDF_UA_TEMPLATE", Path(__file__).parent / "templates" / "pdf_ua.tex")
)
ENGINE = os.environ.get("PDF_UA_ENGINE", "lualatex")
RENDER_TIMEOUT_SECONDS = int(os.environ.get("PDF_UA_TIMEOUT_SECONDS", "150"))
MAX_AST_BYTES = int(os.environ.get("PDF_UA_MAX_AST_BYTES", str(20 * 1024 * 1024)))
# Keep the tail of a LaTeX log: the first error is what callers need, and a
# full log would bloat the error response.
STDERR_TAIL_CHARS = 2000
RAW_TEX_FORMATS = {"latex", "tex", "context"}

LOG = logging.getLogger("pdf_ua_render")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="pdf-ua-render")


def _run_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else ""


def _latex_kernel() -> str:
    """Kernel release date and patch level, read from the installed latex.ltx."""
    path = _run_version(["kpsewhich", "latex.ltx"])
    if not path:
        return ""
    try:
        source = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    release = re.search(r"\\edef\\fmtversion\s*\{([^}]+)\}", source)
    patch = re.search(r"\\def\\patch@level\s*\{([^}]+)\}", source)
    if not release:
        return ""
    return f"{release.group(1)} pl{patch.group(1)}" if patch else release.group(1)


@lru_cache(maxsize=1)
def toolchain() -> dict[str, str]:
    """Versions of everything that shapes the PDF; computed once per process."""
    pandoc = _run_version(["pandoc", "--version"]).removeprefix("pandoc").strip()
    engine = _run_version([ENGINE, "--version"])
    template_sha256 = (
        hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest() if TEMPLATE_PATH.is_file() else ""
    )
    return {
        "pandoc": pandoc,
        "engine": ENGINE,
        "engine_version": engine,
        "latex_kernel": _latex_kernel(),
        "template_sha256": template_sha256,
    }


def _missing_components() -> list[str]:
    missing = [name for name in ("pandoc", ENGINE) if shutil.which(name) is None]
    if not TEMPLATE_PATH.is_file():
        missing.append(f"template {TEMPLATE_PATH}")
    return missing


@app.get("/health")
def health() -> dict[str, Any]:
    missing = _missing_components()
    if missing:
        raise HTTPException(status_code=503, detail=f"missing: {', '.join(missing)}")
    return {"status": "ok", "version": SERVER_VERSION}


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": SERVER_VERSION, **toolchain(), "capability": "document.export.pdf_ua"}


def _raw_tex_nodes(node: Any) -> int:
    """Count RawBlock/RawInline nodes carrying TeX anywhere in the AST."""
    if isinstance(node, list):
        return sum(_raw_tex_nodes(item) for item in node)
    if not isinstance(node, dict):
        return 0
    if node.get("t") in {"RawBlock", "RawInline"}:
        content = node.get("c")
        if isinstance(content, list) and content and str(content[0]).lower() in RAW_TEX_FORMATS:
            return 1
    return sum(_raw_tex_nodes(value) for value in node.values())


def _parse_ast(content: bytes) -> dict[str, Any]:
    if not content:
        raise HTTPException(status_code=400, detail="empty AST payload")
    if len(content) > MAX_AST_BYTES:
        raise HTTPException(status_code=413, detail=f"AST exceeds {MAX_AST_BYTES} bytes")
    try:
        ast = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"AST is not valid JSON: {exc}") from exc
    if not isinstance(ast, dict) or "blocks" not in ast or "pandoc-api-version" not in ast:
        raise HTTPException(
            status_code=400, detail="payload is not a Pandoc JSON AST (blocks, pandoc-api-version)"
        )
    if _raw_tex_nodes(ast.get("blocks")):
        raise HTTPException(status_code=422, detail="raw TeX blocks are not accepted")
    return ast


def _sandboxed_env(workdir: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Writes are confined to the working directory ("paranoid"). Reads are not:
    # in LuaTeX openin_any also gates Lua's io.open, and luaotfload must read
    # Unicode data files by absolute path. Rejecting raw TeX covers reads.
    env.update(openout_any="p", shell_escape="f", TMPDIR=str(workdir))
    return env


@app.post("/render")
def render(
    file: Annotated[UploadFile, File(description="Pandoc JSON AST")],
    lang: Annotated[str, Form(description="BCP 47 document language")] = "pt-BR",
) -> Response:
    content = file.file.read()
    _parse_ast(content)
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", lang):
        raise HTTPException(status_code=400, detail=f"invalid language tag: {lang!r}")
    missing = _missing_components()
    if missing:
        raise HTTPException(status_code=503, detail=f"missing: {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="pdf-ua-") as tmp:
        workdir = Path(tmp)
        output = workdir / "document.pdf"
        command = [
            "pandoc",
            "--from",
            "json",
            "--to",
            "pdf",
            "--standalone",
            "--syntax-highlighting=none",
            f"--pdf-engine={ENGINE}",
            f"--template={TEMPLATE_PATH}",
            "-V",
            f"lang={lang}",
            "-o",
            str(output),
        ]
        try:
            result = subprocess.run(
                command,
                input=content,
                capture_output=True,
                cwd=workdir,
                env=_sandboxed_env(workdir),
                timeout=RENDER_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=504, detail=f"rendering exceeded {RENDER_TIMEOUT_SECONDS}s"
            ) from exc
        if result.returncode != 0 or not output.is_file():
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            LOG.warning("pandoc failed (%s): %s", result.returncode, stderr[-STDERR_TAIL_CHARS:])
            raise HTTPException(
                status_code=422, detail=f"pandoc failed: {stderr[-STDERR_TAIL_CHARS:]}"
            )
        pdf = output.read_bytes()

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"X-Pdf-Standard": "ua-1", "X-Pdf-Engine": ENGINE},
    )
