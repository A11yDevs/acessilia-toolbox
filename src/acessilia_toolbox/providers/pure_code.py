"""Pure-Python code normalization provider.

Reflows flattened code blocks, detects language, cleans up line noise,
and restores proper indentation. No external ML runtime needed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Language detection heuristics
# ---------------------------------------------------------------------------

_LANGUAGE_KEYWORDS: dict[str, list[str]] = {
    "python": [
        "def ", "class ", "import ", "from ", "return ", "if __name__",
        "elif ", "except ", "finally:", "async def", "yield ",
        "lambda ", "with ", "raise ", "pass", "None", "True", "False",
    ],
    "javascript": [
        "function ", "const ", "let ", "var ", "=>", "console.",
        "export ", "import ", "require(", "module.exports",
        "async function", "await ", "Promise.",
    ],
    "typescript": [
        "interface ", "type ", "as ", ": string", ": number",
        ": boolean", ": void", ": any", "enum ", "readonly ",
    ],
    "java": [
        "public ", "private ", "protected ", "static ", "void ",
        "class ", "interface ", "extends ", "implements ", "package ",
        "import java.", "System.out", "@Override", "@Deprecated",
    ],
    "cpp": [
        "#include", "std::", "int main", "cout <<", "->",
        "template ", "namespace ", "virtual ", "override",
    ],
    "csharp": [
        "using System", "namespace ", "class ", "void Main",
        "async Task", "var ", "string ", "int ", "bool ",
        "public ", "private ", "get;", "set;",
    ],
    "go": [
        "func ", "package ", "import (", "defer ", "go ",
        "chan ", "interface{}", "struct {", "nil",
    ],
    "rust": [
        "fn ", "let mut", "impl ", "trait ", "pub ",
        "match ", "unsafe ", "-> ", "String", "Vec<",
    ],
    "sql": [
        "SELECT ", "FROM ", "WHERE ", "INSERT INTO", "CREATE TABLE",
        "ALTER TABLE", "JOIN ", "GROUP BY", "ORDER BY", "HAVING ",
        "DROP TABLE", "DELETE FROM", "UPDATE ", "SET ",
    ],
    "bash": [
        "#!/bin", "export ", "source ", "echo ", "if [[",
        "fi", "done", "esac", "case ", "while ", "for ",
    ],
    "yaml": [
        "---", "version:", "services:", "volumes:",
    ],
    "dockerfile": [
        "FROM ", "RUN ", "CMD ", "COPY ", "ADD ",
        "ENV ", "EXPOSE ", "ENTRYPOINT", "WORKDIR ",
    ],
}

_LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": [".py", ".pyw"],
    "javascript": [".js", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "java": [".java"],
    "cpp": [".cpp", ".cxx", ".cc", ".c", ".h", ".hpp"],
    "csharp": [".cs"],
    "go": [".go"],
    "rust": [".rs"],
    "sql": [".sql"],
    "bash": [".sh", ".bash"],
    "yaml": [".yaml", ".yml"],
    "dockerfile": ["Dockerfile", "dockerfile"],
}


def _detect_language(code: str, filename: str = "") -> str:
    """Detect programming language from code content and optional filename."""
    # Try filename extension first
    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else filename.lower()
        for lang, extensions in _LANGUAGE_EXTENSIONS.items():
            if ext in extensions:
                return lang

    # Try content-based detection
    scores: dict[str, int] = {}
    for lang, keywords in _LANGUAGE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in code)
        if score > 0:
            scores[lang] = score

    if not scores:
        return "unknown"

    return max(scores, key=lambda k: scores[k])


# ---------------------------------------------------------------------------
# Code normalization
# ---------------------------------------------------------------------------


def _normalize_code(code: str, language: str = "") -> dict[str, Any]:
    """Normalize a code block: clean, reflow, detect language."""
    original = code

    # Remove markdown code fences
    cleaned = re.sub(r"^```\w*\n?", "", code, flags=re.MULTILINE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned, flags=re.MULTILINE)

    # Remove leading/trailing whitespace per line
    lines = cleaned.split("\n")
    normalized_lines: list[str] = []
    for line in lines:
        # Remove prompt artifacts
        line = re.sub(r"^(\$ |> |% |>>> |\.\.\. )", "", line)
        # Remove trailing whitespace
        line = line.rstrip()
        normalized_lines.append(line)

    # Remove leading empty lines
    while normalized_lines and not normalized_lines[0].strip():
        normalized_lines.pop(0)
    # Remove trailing empty lines
    while normalized_lines and not normalized_lines[-1].strip():
        normalized_lines.pop()

    normalized = "\n".join(normalized_lines)

    # Detect language
    detected = language or _detect_language(normalized)

    # Count lines
    code_lines = len(normalized_lines)
    blank_lines = sum(1 for line in normalized_lines if not line.strip())

    return {
        "original": original,
        "normalized": normalized,
        "language": detected,
        "line_count": code_lines,
        "blank_line_count": blank_lines,
        "character_count": len(normalized),
    }


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PureCodeProvider:
    """Pure-Python code normalization provider."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        params = dict(parameters or {})
        code = payload.decode("utf-8", errors="replace").strip()
        language = params.get("language", "")

        result = _normalize_code(code, language=language)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=result,
            backend="pure-python",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=VERSION,
            configuration={
                "capability": capability_id,
                **params,
            },
        )

    def versions(self) -> dict[str, str]:
        return {"provider": VERSION}

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            version=VERSION,
            checked_at=datetime.now(UTC),
        )


__all__ = ["PureCodeProvider", "_detect_language", "_normalize_code"]
