"""Resolve real Compose files without a Docker daemon; exercise promotion gates."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "production_deploy", ROOT / "scripts/production_deploy.py"
)
assert SPEC and SPEC.loader
deploy: ModuleType = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


@pytest.fixture
def production(tmp_path: Path) -> tuple[Path, dict]:
    for source, name in (
        ("docker-compose.production.yml", "docker-compose.production.yml"),
        ("deploy/production/providers-config.yaml", "providers-config.yaml"),
        ("deploy/production/deploy.env.example", ".deploy.env"),
    ):
        shutil.copyfile(ROOT / source, tmp_path / name)
    runtime = (ROOT / "deploy/production/runtime.env.example").read_text()
    for key, value in {
        "TOOLBOX_API_KEY": "production-test-only",
        "VLM_API_KEY": "gateway-test-only",
        "VLM_SERVE_URL": "http://gateway.invalid:8002/v1/vlm",
        "MINIO_ACCESS_KEY": "minio-test-only",
        "MINIO_SECRET_KEY": "minio-test-secret-only",
    }.items():
        runtime = runtime.replace(f"{key}=\n", f"{key}={value}\n")
    (tmp_path / ".env").write_text(runtime)
    result = subprocess.run(
        [*deploy.compose_command(tmp_path), "config", "--format", "json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    return tmp_path, json.loads(result.stdout)


def test_production_resolves_without_host_ports_or_deployment_secret_leaks(production):
    directory, config = production
    deploy.check_config(config, directory)
    assert len(config["services"]) == 12
    assert not {"nougat-serve", "audiveris-serve"} & set(config["services"])
    assert all(not service.get("ports") for service in config["services"].values())
    assert "PRODUCTION_TOOLBOX_IMAGE" not in config["services"]["toolbox"]["environment"]
    assert "VLM_MAX_CONCURRENCY" in config["services"]["toolbox"]["environment"]


@pytest.mark.parametrize(
    "defect",
    [
        "tag",
        "staging-name",
        "published-port",
        "host-network",
        "sidecar-backplane",
        "missing-alias",
        "missing-key",
        "no-auth",
        "raw-model",
        "registry-secret",
        "minio-image-drift",
        "missing-registry",
        "extra-service",
    ],
)
def test_preflight_rejects_unsafe_or_incompatible_stack(production, defect):
    directory, original = production
    config = copy.deepcopy(original)
    services = config["services"]
    toolbox = services["toolbox"]
    env = toolbox["environment"]
    if defect == "tag":
        toolbox["image"] = "ghcr.io/a11ydevs/acessilia-toolbox:main"
    elif defect == "staging-name":
        toolbox["container_name"] = "acessilia-toolbox-staging"
    elif defect == "published-port":
        services["valkey"]["ports"] = [{"target": 6379, "published": "6379"}]
    elif defect == "host-network":
        toolbox["network_mode"] = "host"
    elif defect == "sidecar-backplane":
        services["mineru-serve"]["networks"]["core-toolbox"] = None
    elif defect == "missing-alias":
        toolbox["networks"]["core-toolbox"]["aliases"] = []
    elif defect == "missing-key":
        env["VLM_API_KEY"] = ""
    elif defect == "no-auth":
        env["TOOLBOX_ALLOW_UNAUTHENTICATED"] = "1"
    elif defect == "raw-model":
        env["VLM_SERVE_URL"] = "http://model.invalid:81"
    elif defect == "registry-secret":
        env["GHCR_TOKEN"] = "must-not-enter-runtime"
    elif defect == "minio-image-drift":
        services["minio-init"]["image"] = "cgr.dev/chainguard/minio@sha256:" + "a" * 64
    elif defect == "missing-registry":
        (directory / "providers-config.yaml").unlink()
    elif defect == "extra-service":
        services["unexpected"] = {"image": "unexpected:latest"}
    with pytest.raises(ValueError):
        deploy.check_config(config, directory)


def test_wrapper_defaults_to_check_and_reports_no_secrets(production):
    directory, _ = production
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/production-update.sh")],
        cwd=directory,
        env={**os.environ, "PRODUCTION_DIR": str(directory)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "verified; no services started" in result.stdout
    assert "production-test-only" not in result.stdout + result.stderr
    assert "gateway-test-only" not in result.stdout + result.stderr


def test_failed_pull_never_activates(production, monkeypatch):
    directory, config = production
    commands = []
    monkeypatch.setattr(deploy, "check", lambda _: config)
    monkeypatch.setattr(
        sys, "argv", ["production_deploy.py", "apply", "--directory", str(directory)]
    )

    def fail_pull(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(deploy.subprocess, "run", fail_pull)
    assert deploy.main() == 1
    assert len(commands) == 1
    assert commands[0][-1] == "pull"


def test_failed_dependency_probe_reports_possible_live_replacement(production, monkeypatch, capsys):
    directory, config = production
    commands = []
    monkeypatch.setattr(deploy, "check", lambda _: config)
    monkeypatch.setattr(
        sys, "argv", ["production_deploy.py", "apply", "--directory", str(directory)]
    )
    monkeypatch.setattr(
        deploy.subprocess, "run", lambda command, **kwargs: commands.append(command)
    )

    def fail_probe(*args):
        raise ValueError("dependency unavailable")

    monkeypatch.setattr(deploy, "verify_running", fail_probe)
    assert deploy.main() == 1
    assert "new containers may remain running" in capsys.readouterr().out
    assert len(commands) == 2
    assert "up" in commands[-1]
    assert not any("prune" in command or "down" in command for command in commands)


def test_staging_only_publishes_authenticated_toolbox_beyond_loopback(tmp_path):
    shutil.copyfile(ROOT / "docker-compose.staging.yml", tmp_path / "docker-compose.staging.yml")
    (tmp_path / ".env").write_text(
        "TOOLBOX_API_KEY=staging-test-only\nMINIO_ACCESS_KEY=minio-test\n"
        "MINIO_SECRET_KEY=minio-test-secret\n"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "staging",
            "--env-file",
            str(tmp_path / ".env"),
            "-f",
            str(tmp_path / "docker-compose.staging.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    assert services["toolbox"]["ports"]
    for name, service in services.items():
        if name != "toolbox":
            for port in service.get("ports", []):
                assert port["host_ip"] == "127.0.0.1", name


@pytest.mark.parametrize("defect", ["image-drift", "init-failed", "unhealthy"])
def test_running_stack_must_match_pins_and_be_healthy_before_acceptance(
    production, monkeypatch, defect
):
    directory, config = production
    containers = {value: name for name, value in deploy.CONTAINERS.items()}
    commands = []

    def inspect(command, _directory):
        if command[1] == "image":
            return "sha256:approved-image-id"
        name = containers[command[-1]]
        state = {"Running": True, "Health": {"Status": "healthy"}}
        if name == "minio-init":
            state = {"Status": "exited", "ExitCode": 0}
        image = "sha256:approved-image-id"
        if defect == "image-drift" and name == "toolbox":
            image = "sha256:unexpected-image-id"
        elif defect == "init-failed" and name == "minio-init":
            state["ExitCode"] = 1
        elif defect == "unhealthy" and name == "mineru-serve":
            state["Health"]["Status"] = "unhealthy"
        return json.dumps([{"Image": image, "State": state}])

    monkeypatch.setattr(deploy, "capture", inspect)
    monkeypatch.setattr(deploy.subprocess, "run", lambda *args, **kwargs: commands.append(args))
    with pytest.raises(ValueError):
        deploy.verify_running(config, directory)
    assert commands == []
