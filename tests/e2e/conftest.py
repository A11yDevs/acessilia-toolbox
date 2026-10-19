"""Load the E2E target from .env.e2e at the repository root, when present.

Only KEY=VALUE lines are read (blank lines and # comments are ignored, and
surrounding quotes are stripped). Variables already set in the environment win,
so CI can inject them directly. See .env.e2e.example.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.e2e"


def _load(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


if ENV_FILE.is_file():
    _load(ENV_FILE)
