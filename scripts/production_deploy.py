"""Validate and explicitly promote a complete digest-pinned production stack."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORIES = {
    "toolbox": "ghcr.io/a11ydevs/acessilia-toolbox",
    "docling-serve": "ghcr.io/docling-project/docling-serve-cpu",
    "mineru-serve": "ghcr.io/a11ydevs/acessilia-toolbox-mineru",
    "rapid-latex-ocr": "ghcr.io/a11ydevs/acessilia-toolbox-rapid-latex-ocr",
    "pp-formulanet": "ghcr.io/a11ydevs/acessilia-toolbox-pp-formulanet",
    "rapidocr-orient": "ghcr.io/a11ydevs/acessilia-toolbox-rapidocr-orient",
    "pdf-ua-render": "ghcr.io/a11ydevs/acessilia-toolbox-pdf-ua-render",
    "verapdf-validate": "ghcr.io/a11ydevs/acessilia-toolbox-verapdf-validate",
    "math-speech": "ghcr.io/a11ydevs/acessilia-toolbox-math-speech",
    "minio": "cgr.dev/chainguard/minio",
    "minio-init": "cgr.dev/chainguard/minio",
    "valkey": "valkey/valkey",
}
CONTAINERS = {
    "toolbox": "acessilia-toolbox-production",
    "minio": "acessilia-minio-production",
    "minio-init": "acessilia-minio-init-production",
    "valkey": "acessilia-valkey-production",
    **{
        name: f"{name}-production"
        for name in REPOSITORIES
        if name not in {"toolbox", "minio", "minio-init", "valkey"}
    },
}

PROBE = """
import json
import os
import urllib.request
headers = {"Authorization": "Bearer " + os.environ["TOOLBOX_API_KEY"]}
for path in ("/v1/providers/docling/health", "/v1/providers/mineru/health", "/v1/vlm/health"):
    request = urllib.request.Request("http://127.0.0.1:8002" + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if not (body.get("healthy") is True or body.get("status") == "healthy"):
        raise SystemExit("Dependency health failed: " + path)
print("Docling, MinerU and authenticated VLM chain verified.")
"""


def compose_command(directory: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        "acessilia-toolbox-production",
        "--env-file",
        str(directory / ".env"),
        "--env-file",
        str(directory / ".deploy.env"),
        "-f",
        str(directory / "docker-compose.production.yml"),
    ]


def capture(command: list[str], directory: Path) -> str:
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False)
    if result.returncode:
        # Compose errors can contain interpolated credentials. Inspect privately.
        raise ValueError("Command failed; inspect configuration privately: " + command[0])
    return result.stdout


def check_config(config: dict, directory: Path) -> dict:
    if config.get("name") != "acessilia-toolbox-production":
        raise ValueError("Keep the existing production Compose project identity")
    services = config.get("services", {})
    if set(services) != set(REPOSITORIES):
        raise ValueError("Unexpected production services; review the complete stack")
    for name, repository in REPOSITORIES.items():
        service = services[name]
        if not re.fullmatch(
            re.escape(repository) + r"@sha256:[0-9a-f]{64}", service.get("image", "")
        ):
            raise ValueError(f"{name} requires an approved repository@sha256 digest")
        if service.get("container_name") != CONTAINERS[name]:
            raise ValueError(f"Unexpected production container identity: {name}")
        if service.get("ports") or service.get("network_mode") == "host":
            raise ValueError(f"Production must not expose host ports: {name}")
        if name != "toolbox" and set(service.get("networks", {})) != {"default"}:
            raise ValueError(f"Keep {name} on the private project network only")
    if services["minio"]["image"] != services["minio-init"]["image"]:
        raise ValueError("MinIO server and bucket initializer must use the same image")
    network = config.get("networks", {}).get("core-toolbox", {})
    if not network.get("external") or not network.get("name"):
        raise ValueError("Use the external network shared with the core")
    if config.get("networks", {}).get("default", {}).get("external"):
        raise ValueError("The auxiliary project network must not be external")
    toolbox = services["toolbox"]
    networks = toolbox.get("networks", {})
    if set(networks) != {"default", "core-toolbox"}:
        raise ValueError("Toolbox must join its private network and the core backplane")
    if "toolbox-production" not in networks["core-toolbox"].get("aliases", []):
        raise ValueError("Missing toolbox-production alias for the core")
    env = toolbox.get("environment", {})
    for key in ("TOOLBOX_API_KEY", "VLM_API_KEY", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        if not env.get(key):
            raise ValueError(f"Set {key} privately in the production runtime .env")
    if str(env.get("TOOLBOX_ALLOW_UNAUTHENTICATED")) != "0":
        raise ValueError("Production Toolbox authentication must be enabled")
    for key in ("VLM_MAX_CONCURRENCY", "VLM_TIMEOUT_SECONDS", "VLM_SLOT_WAIT_SECONDS"):
        if int(env.get(key, "0")) < 1:
            raise ValueError(f"{key} must be positive")
    if any(
        key.startswith("PRODUCTION_") or key in {"GHCR_TOKEN", "TRACK_BRANCH", "TRACK_TAG"}
        for key in env
    ):
        raise ValueError("Keep deployment and registry settings out of runtime .env")
    upstream = urlsplit(env.get("VLM_SERVE_URL", ""))
    if (
        upstream.scheme not in {"http", "https"}
        or not upstream.hostname
        or upstream.path.rstrip("/") != "/v1/vlm"
        or upstream.username
        or upstream.password
        or upstream.query
        or upstream.fragment
    ):
        raise ValueError("VLM_SERVE_URL must address the gateway's /v1/vlm endpoint")
    mounts = [
        v for v in toolbox.get("volumes", []) if v.get("target") == "/app/providers-config.yaml"
    ]
    if (
        len(mounts) != 1
        or mounts[0].get("type") != "bind"
        or not mounts[0].get("read_only")
        or mounts[0].get("bind", {}).get("create_host_path", False)
        or Path(mounts[0]["source"]).resolve() != directory / "providers-config.yaml"
        or not (directory / "providers-config.yaml").is_file()
    ):
        raise ValueError("Mount the reviewed production providers-config.yaml read-only")
    return config


def check(directory: Path) -> dict:
    config = json.loads(
        capture([*compose_command(directory), "config", "--format", "json"], directory)
    )
    check_config(config, directory)
    print("Production Toolbox configuration verified; no services started.")
    return config


def verify_running(config: dict, directory: Path) -> None:
    for name, service in config["services"].items():
        expected = capture(
            ["docker", "image", "inspect", service["image"], "--format", "{{.Id}}"], directory
        ).strip()
        current = json.loads(capture(["docker", "inspect", CONTAINERS[name]], directory))[0]
        if not expected or current["Image"] != expected:
            raise ValueError(f"Running image differs from the approved digest: {name}")
        state = current["State"]
        if name == "minio-init":
            if state.get("Status") != "exited" or state.get("ExitCode") != 0:
                raise ValueError("MinIO bucket initialization did not finish successfully")
        elif not state.get("Running") or state.get("Health", {}).get("Status") != "healthy":
            raise ValueError(f"Production service is not healthy: {name}")
    subprocess.run(
        ["docker", "exec", "-i", CONTAINERS["toolbox"], "python", "-"],
        cwd=directory,
        input=PROBE,
        text=True,
        check=True,
        timeout=100,
    )
    print(
        "Running production images and dependencies verified; document acceptance remains required."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "apply", "verify"))
    parser.add_argument("--directory", type=Path, default=Path("/opt/acessilia/toolbox"))
    args = parser.parse_args()
    directory = args.directory.resolve()
    try:
        config = check(directory)
        if args.command == "apply":
            # Pull the entire pinned set before replacing anything. Failed pull leaves
            # running containers alone. Never prune images/volumes or write success caches.
            subprocess.run([*compose_command(directory), "pull"], cwd=directory, check=True)
            try:
                subprocess.run(
                    [
                        *compose_command(directory),
                        "up",
                        "-d",
                        "--pull",
                        "never",
                        "--wait",
                        "--wait-timeout",
                        "900",
                    ],
                    cwd=directory,
                    check=True,
                )
                verify_running(config, directory)
            except (ValueError, subprocess.SubprocessError):
                print(
                    "Activation failed; new containers may remain running. Keep ingress suspended."
                )
                print("Inspect services and follow manual rollback; no data was restored.")
                raise
        elif args.command == "verify":
            verify_running(config, directory)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"Production Toolbox check failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
