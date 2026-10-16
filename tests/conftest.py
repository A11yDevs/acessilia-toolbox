"""Test-wide configuration.

The application refuses to start without TOOLBOX_API_KEY unless running
unauthenticated is asked for explicitly. Most suites build an app without a
key, so they opt in here; the tests of that rule clear the variable themselves
(tests/integration/test_rest_api.py).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _allow_unauthenticated_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
