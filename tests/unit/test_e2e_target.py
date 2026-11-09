"""The E2E target never leaks into the environment of the other tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.e2e import target


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / ".env.e2e"
    path.write_text(
        "# comment\n\nTOOLBOX_BASE_URL='https://e2e.example/toolbox'\n"
        'TOOLBOX_API_KEY="secret"\nnot a pair\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(target, "ENV_FILE", path)
    target._file_values.cache_clear()
    yield path
    target._file_values.cache_clear()


def test_values_come_from_the_file_without_touching_the_environment(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TOOLBOX_BASE_URL", raising=False)
    monkeypatch.delenv("TOOLBOX_API_KEY", raising=False)
    before = dict(os.environ)

    assert target.e2e_setting("TOOLBOX_BASE_URL") == "https://e2e.example/toolbox"
    assert target.e2e_setting("TOOLBOX_API_KEY") == "secret"
    assert target.e2e_setting("MISSING", "fallback") == "fallback"
    assert dict(os.environ) == before


def test_the_environment_wins_over_the_file(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOOLBOX_API_KEY", "from-ci")
    assert target.e2e_setting("TOOLBOX_API_KEY") == "from-ci"


def test_a_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert target.read_env_file(tmp_path / "absent") == {}


def test_no_conftest_exports_the_e2e_file() -> None:
    """A conftest runs at collection for the whole session; it must not load it."""
    e2e_dir = Path(target.__file__).parent
    assert not (e2e_dir / "conftest.py").exists()
