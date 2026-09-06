"""Packaging guarantees for the distributed package."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import acessilia_toolbox

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "acessilia_toolbox"


def test_package_exposes_a_version() -> None:
    assert acessilia_toolbox.__version__


def test_package_ships_a_py_typed_marker() -> None:
    assert (SOURCE_ROOT / "py.typed").is_file()


@pytest.mark.skipif(
    not (PROJECT_ROOT / ".git").exists(),
    reason="not a git checkout",
)
def test_no_source_file_is_excluded_by_gitignore() -> None:
    """A stock `MANIFEST` rule once matched `manifest/` on case-insensitive
    filesystems and silently dropped a whole package from version control."""
    tracked_candidates = [
        str(path.relative_to(PROJECT_ROOT))
        for path in SOURCE_ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    assert tracked_candidates, "no source files found"

    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(tracked_candidates),
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )

    ignored = [line for line in result.stdout.splitlines() if line]
    assert not ignored, f"source files excluded by .gitignore: {ignored}"
