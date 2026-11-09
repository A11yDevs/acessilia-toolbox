"""Local MinerU builds and the sidecar workflow share the repository context."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "filename",
    ["mineru-cpu.Dockerfile", "mineru-cpu-repair.Dockerfile", "mineru-gpu-basic.Dockerfile"],
)
def test_mineru_patch_is_available_in_the_ci_build_context(filename: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/sidecars.yml").read_text())
    publish = next(
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    )
    assert publish["with"]["context"] == "."
    copies = [
        line.split()[1]
        for line in (ROOT / "docker" / filename).read_text().splitlines()
        if line.startswith("COPY ")
    ]
    assert copies
    assert all((ROOT / source).is_file() for source in copies)


@pytest.mark.parametrize(
    ("variant", "repair", "filename"),
    [
        ("cpu", "0", "mineru-cpu.Dockerfile"),
        ("cpu", "1", "mineru-cpu-repair.Dockerfile"),
        ("gpu-basic", "0", "mineru-gpu-basic.Dockerfile"),
    ],
)
def test_local_build_uses_the_same_context_as_ci(
    tmp_path: Path, variant: str, repair: str, filename: str
) -> None:
    executable = tmp_path / "docker"
    calls = tmp_path / "calls.jsonl"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['MINERU_BUILD_CALLS'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    executable.chmod(0o755)
    subprocess.run(
        ["bash", str(ROOT / "scripts/build-mineru-image.sh"), "4.0.12"],
        cwd=ROOT,
        env=os.environ
        | {
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "MINERU_VARIANT": variant,
            "MINERU_REPAIR": repair,
            "MINERU_BUILD_CALLS": str(calls),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    builds = [
        args
        for line in calls.read_text().splitlines()
        if (args := json.loads(line))[0] == "build"
    ]
    assert len(builds) == 1
    build = builds[0]
    assert Path(build[-1]) == ROOT
    assert Path(build[build.index("-f") + 1]) == ROOT / "docker" / filename


def test_production_registry_and_compose_match_the_mineru4_adapter() -> None:
    config = yaml.safe_load(
        (ROOT / "deploy/providers-config.default.yaml").read_text()
    )
    providers = {entry["id"]: entry for entry in config["providers"]}
    for provider_id in ("mineru", "mineru-layout", "mineru-ocr"):
        entry = providers[provider_id]
        assert entry["version"] == "4.0.12"
        assert entry["health_path"] == "/v1/health"
        assert entry["config"]["tier"] == "basic"

    compose = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text())
    service = compose["services"]["mineru-serve"]
    assert service["entrypoint"] == [
        "mineru-kit",
        "api-server",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--tier",
        "basic",
    ]
    assert service["environment"]["MINERU_MODEL_SMALL_BACKEND"] == (
        "${MINERU_MODEL_SMALL_BACKEND:-onnx}"
    )
    assert "mineru-models:/home/mineru/.mineru" in service["volumes"]
    assert service["healthcheck"]["test"][-1].endswith("/v1/health')")
    assert "/openapi.json" not in str(service)
