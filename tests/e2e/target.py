"""The E2E target: settings from the environment, else from .env.e2e.

The file at the repository root is read on demand and never copied into
``os.environ``. Copying it at collection time (as a conftest used to) leaked
``TOOLBOX_API_KEY`` into every other test in the session, which then expected
authentication. Only KEY=VALUE lines are read (blank lines and # comments are
ignored, and surrounding quotes are stripped). Variables already set in the
environment win, so CI can inject them directly. See .env.e2e.example.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.e2e"


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE pairs of ``path``; empty when the file does not exist."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


@lru_cache(maxsize=1)
def _file_values() -> dict[str, str]:
    return read_env_file(ENV_FILE)


def e2e_setting(name: str, default: str = "") -> str:
    """``name`` from the environment, else from .env.e2e, else ``default``."""
    return os.environ.get(name) or _file_values().get(name, default)
