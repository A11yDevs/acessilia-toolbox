"""Regression test: no Portuguese (pt-BR) text in code or documentation.

Strings in fixture datasets, versioned snapshots, and functional detection
patterns (callout titles, chapter-heading regex) are exempt — the former
are real document content, the latter are language-agnostic classifiers.

Add new exemptions here when adding intentional Portuguese detection patterns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXEMPT_PATHS = {
    # Real document content — not documentation.
    PROJECT_ROOT / "tests" / "fixtures" / "dataset",
    PROJECT_ROOT / "tests" / "snapshot" / "snapshots",
}

# Known-functional Portuguese strings in source code. The tuple is
# (relative_file_path, substring) — add new entries here when adding
# intentional detection patterns.
EXEMPT_STRINGS: list[tuple[str, str]] = [
    # Callout detection — multi-language set
    ("src/acessilia_toolbox/core/normalization/builder.py", "dica"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "importante"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "atenção"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "aviso"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "nota"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "observação"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "observacao"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "informação"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "informacao"),
    # Chapter heading detection regex
    ("src/acessilia_toolbox/core/normalization/builder.py", "CAP[ÍI]TULO"),
    # Multi-language callout titles with accents (Spanish)
    ("src/acessilia_toolbox/core/normalization/builder.py", "observación"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "atención"),
    ("src/acessilia_toolbox/core/normalization/builder.py", "información"),
    # Schema description — canonical name
    ("src/acessilia_toolbox/core/normalization/models.py", "Acessilia structural extractor"),
    ("schemas/structured-document@1.json", "Acessilia structural extractor"),
]

# Files with known fixture or generated content that may contain Portuguese.
EXEMPT_FILES: set[str] = {
    "CONTRIBUTING.md",
    "docs/dev-workflow.md",
    "docs/auto-update.md",
    "docker-compose.staging.yml",
}

# Build the exempt set from strings so we can check quickly.
_EXEMPT_BY_FILE: dict[str, set[str]] = {}
for file_rel, substring in EXEMPT_STRINGS:
    _EXEMPT_BY_FILE.setdefault(file_rel, set()).add(substring)

# Add exempt files path strings
EXEMPT_FILE_PATHS = {
    "tests/fixtures/dataset/",
    "tests/snapshot/snapshots/",
}


def _iter_project_files(root: Path) -> list[Path]:
    """Walk source, docs and config files (skip exempt and __pycache__)."""
    files: list[Path] = []
    for pattern in ("src/**/*.py", "docs/**/*.md", "schemas/*.json",
                    "capabilities/*.yaml", "pyproject.toml", "Dockerfile",
                    ".dockerignore", ".env.example", ".env.docker",
                    "providers-config.yaml", "README.md", "CONTRIBUTING.md",
                    ".github/workflows/*.yml", "docker-compose*.yml",
                    "scripts/*.sh", "scripts/*.py"):
        found = list(root.glob(pattern))
        for p in found:
            rel = p.relative_to(root)
            if any(rel.is_relative_to(ex) for ex in EXEMPT_PATHS):
                continue
            if any(str(rel).startswith(prefix) for prefix in EXEMPT_FILE_PATHS):
                continue
            if "__pycache__" in p.parts:
                continue
            files.append(p)
    return files


# Patterns that match a Portuguese (accented) character.
# Only check .py, .md, .json, .yaml, .toml files for accented strings.
_PORTUGUESE_RE = pytest.importorskip("re").compile(r"[áàâãéèêíìóòôõúùûç]")


@pytest.fixture(scope="session")
def project_files() -> list[Path]:
    return _iter_project_files(PROJECT_ROOT)


def test_no_portuguese_in_code(project_files: list[Path]) -> None:
    """No .py file should contain accented Portuguese strings,
    except known exemptions in EXEMPT_STRINGS."""
    failures: list[str] = []
    for filepath in project_files:
        if filepath.suffix not in {".py", ".md", ".json", ".yaml", ".yml", ".toml"}:
            continue
        if filepath.name == "pyproject.toml" and filepath.parent == PROJECT_ROOT:
            continue

        rel = str(filepath.relative_to(PROJECT_ROOT))
        if rel in EXEMPT_FILES:
            continue
        exempt_lines = _EXEMPT_BY_FILE.get(rel, set())

        try:
            text = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            match = _PORTUGUESE_RE.search(line_stripped)
            if not match:
                continue
            # Check if this line contains an exempt substring
            if any(exempt in line_stripped for exempt in exempt_lines):
                continue
            failures.append(f"  {rel}:{lineno}: {line_stripped[:100]}")

    if failures:
        fail_msg = (
            f"Found {len(failures)} line(s) with Portuguese text.\n"
            "If intentional, add an entry to EXEMPT_STRINGS in this test file.\n"
            + "\n".join(failures)
        )
        pytest.fail(fail_msg)


def test_exempt_paths_still_exist() -> None:
    """Fail early if an exempt path is renamed or deleted."""
    for path in EXEMPT_PATHS:
        assert path.exists(), f"Exempt path {path} no longer exists"
    for rel in EXEMPT_FILES:
        path = PROJECT_ROOT / rel
        assert path.exists(), f"Exempt file {rel} no longer exists"
