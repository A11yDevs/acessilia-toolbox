"""The version the Toolbox reports is the one pyproject.toml releases.

bhs start bumps pyproject.toml; the API health, MCP and CLI report
``__version__`` and docs/openapi.json repeats it, so a stale copy would make a
release report the previous version.
"""

import json
import tomllib
from pathlib import Path

from acessilia_toolbox import __version__

ROOT_DIR = Path(__file__).resolve().parents[2]


def _project_version() -> str:
    with (ROOT_DIR / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def test_package_reports_the_pyproject_version():
    assert __version__ == _project_version()


def test_committed_openapi_reports_the_pyproject_version():
    openapi = json.loads((ROOT_DIR / "docs" / "openapi.json").read_text())

    assert openapi["info"]["version"] == _project_version()
