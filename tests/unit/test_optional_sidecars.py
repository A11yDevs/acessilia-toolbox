"""Optional sidecars start only under a Compose profile, and only then are announced (#108).

``audiveris-serve`` (profile ``music``) and ``nougat-serve`` (profile
``nougat``) have no client in the Acessilia core. By default the local and
staging Compose files do not start them, and the toolbox loads
``deploy/providers-config.default.yaml``, which does not register their
providers, so the catalogue, health and calls agree with what is running.
Production mounts the same file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import ProviderNotFoundError
from acessilia_toolbox.core.provider import ProviderRegistry

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.staging.yml")
PROFILES = {"audiveris-serve": "music", "nougat-serve": "nougat"}
DEFAULT_CONFIG_IN_IMAGE = "/app/deploy/providers-config.default.yaml"
FULL_CONFIG_IN_IMAGE = "/app/providers-config.yaml"


def _deriver():
    spec = importlib.util.spec_from_file_location(
        "derive_providers_config", ROOT / "scripts" / "derive_providers_config.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


# ── The derived provider file ──────────────────────────────────────────────


def test_the_default_provider_file_is_up_to_date() -> None:
    assert _deriver().main(["--check"]) == 0, (
        "deploy/providers-config.default.yaml is stale: "
        "run python scripts/derive_providers_config.py"
    )


def test_the_default_provider_file_is_the_full_one_without_the_optional_providers() -> None:
    full = yaml.safe_load((ROOT / "providers-config.yaml").read_text(encoding="utf-8"))
    default = yaml.safe_load(
        (ROOT / "deploy" / "providers-config.default.yaml").read_text(encoding="utf-8")
    )
    optional = set(_deriver().OPTIONAL_PROVIDERS)

    assert optional == {"audiveris", "nougat"}
    assert default["providers"] == [p for p in full["providers"] if p["id"] not in optional]


def test_an_optional_provider_missing_from_the_full_file_is_an_error() -> None:
    with pytest.raises(ValueError, match="optional providers not in"):
        _deriver().derive("providers:\n  - id: docling\n    capabilities: [x.y]\n")


# ── Compose ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_optional_sidecars_start_only_under_their_profile(compose_file: str) -> None:
    services = _compose(compose_file)["services"]

    for service, profile in PROFILES.items():
        assert services[service]["profiles"] == [profile], (compose_file, service)


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_toolbox_does_not_depend_on_an_optional_sidecar(compose_file: str) -> None:
    """A depends_on on a disabled service would stop the toolbox from starting."""
    depends_on = _compose(compose_file)["services"]["toolbox"].get("depends_on") or {}

    assert not set(depends_on) & set(PROFILES), compose_file


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_toolbox_loads_the_default_provider_file_unless_told_otherwise(
    compose_file: str,
) -> None:
    environment = _compose(compose_file)["services"]["toolbox"]["environment"]

    assert environment["TOOLBOX_PROVIDERS_CONFIG"] == (
        "${TOOLBOX_COMPOSE_PROVIDERS_CONFIG:-" + DEFAULT_CONFIG_IN_IMAGE + "}"
    )
    # Both files ship in the image: the Dockerfile copies the repository.
    assert (ROOT / DEFAULT_CONFIG_IN_IMAGE.removeprefix("/app/")).is_file()
    assert (ROOT / FULL_CONFIG_IN_IMAGE.removeprefix("/app/")).is_file()


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_container_ignores_the_providers_config_set_for_runs_outside_docker(
    compose_file: str,
) -> None:
    """.env.example sets TOOLBOX_PROVIDERS_CONFIG=providers-config.yaml for runs
    outside Docker; Compose would interpolate it from .env and load the full file
    in the container, announcing sidecars that are not running."""
    dotenv = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nTOOLBOX_PROVIDERS_CONFIG=providers-config.yaml\n" in dotenv
    value = _compose(compose_file)["services"]["toolbox"]["environment"][
        "TOOLBOX_PROVIDERS_CONFIG"
    ]
    assert "${TOOLBOX_PROVIDERS_CONFIG" not in value
    assert "TOOLBOX_COMPOSE_PROVIDERS_CONFIG" not in dotenv


def test_production_does_not_declare_the_optional_sidecars() -> None:
    assert not set(_compose("docker-compose.production.yml")["services"]) & set(PROFILES)


# ── What a client sees with the default file ───────────────────────────────


@pytest.fixture
def default_mode(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    capabilities = CapabilityRegistry.from_directory(ROOT / "capabilities")
    providers = ProviderRegistry.from_file(ROOT / "deploy" / "providers-config.default.yaml")
    return TestClient(create_app(capabilities, providers))


def test_the_catalogue_does_not_announce_the_optional_providers(default_mode) -> None:
    catalogue = {
        (entry["id"], entry["version"]): entry["providers"]
        for entry in default_mode.get("/v1/capabilities").json()
    }
    providers = {entry["id"] for entry in default_mode.get("/v1/providers").json()}

    assert "nougat" not in catalogue[("document.structure.extract", 1)]
    assert "docling" in catalogue[("document.structure.extract", 1)]
    # music.omr stays served in-process by homr; only the Audiveris sidecar goes.
    assert catalogue[("music.omr", 1)] == ["homr"]
    assert not providers & {"audiveris", "nougat"}


def test_asking_for_a_disabled_optional_provider_is_a_clear_error() -> None:
    providers = ProviderRegistry.from_file(ROOT / "deploy" / "providers-config.default.yaml")

    with pytest.raises(ProviderNotFoundError) as raised:
        providers.resolve("document.structure.extract", "nougat")
    assert raised.value.http_status == 404
