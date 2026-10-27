"""Readiness must verify the structural capability binding before health."""

import pytest
from tests.integration.test_artifacts_api import client as client_fixture
from tests.integration.test_artifacts_api import store as store_fixture

from acessilia_toolbox.core.provider import ProviderDescriptor

client = client_fixture
store = store_fixture


@pytest.mark.parametrize("configured_by", ["query", "environment"])
def test_readiness_rejects_healthy_unbound_provider(client, monkeypatch, configured_by):
    client.app.state.providers.register(
        ProviderDescriptor(
            id="layout-only",
            transport="http",
            endpoint="http://healthy.test",
            capabilities=["document.layout.analyze"],
        )
    )

    def forbidden(*args, **kwargs):
        pytest.fail("unbound provider must be rejected before HTTP health probing")

    monkeypatch.setattr("acessilia_toolbox.api.rest.httpx.Client", forbidden)
    if configured_by == "environment":
        monkeypatch.setenv("TOOLBOX_READY_PROVIDER", "layout-only")
        params = {}
    else:
        params = {"provider": "layout-only"}
    response = client.get("/v1/ready", params=params)
    assert response.status_code == 503
    assert response.json()["checks"] == {
        "catalog": True,
        "structure": False,
        "storage": True,
    }
