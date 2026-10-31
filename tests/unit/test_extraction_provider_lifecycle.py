"""Extraction providers recover after process exit without losing model caches."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.staging.yml"])
@pytest.mark.parametrize("provider", ["docling-serve", "mineru-serve"])
def test_extraction_providers_restart_with_healthchecks_and_named_caches(filename, provider):
    compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
    service = compose["services"][provider]

    assert service.get("restart") == "unless-stopped"
    assert service["healthcheck"]["test"][0] == "CMD"
    assert service["healthcheck"]["start_period"]
    assert service["healthcheck"]["retries"] > 0
    for mount in service["volumes"]:
        volume = mount.split(":", 1)[0]
        assert volume in compose["volumes"]


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.staging.yml"])
def test_mineru_memory_budget_is_deployment_configurable(filename):
    compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
    service = compose["services"]["mineru-serve"]

    assert service["mem_limit"] == "${MINERU_MEMORY_LIMIT:-0}"
    assert not service.get("oom_kill_disable", False)
