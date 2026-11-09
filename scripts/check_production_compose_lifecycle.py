"""Exercise the production activation command on disposable real containers."""

from __future__ import annotations

import json
import subprocess
import tempfile
import uuid
from pathlib import Path

from production_deploy import activation_command


def check(exit_code: int) -> None:
    project = "acessilia-compose-smoke-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        directory = Path(temporary)
        config = {
            "services": {
                "minio-init": {
                    "image": "busybox:1.37.0",
                    "command": ["sh", "-c", f"sleep 2; touch /state/ready; exit {exit_code}"],
                    "volumes": ["state:/state"],
                    "restart": "no",
                },
                "toolbox": {
                    "image": "busybox:1.37.0",
                    "command": ["sleep", "300"],
                    "volumes": ["state:/state"],
                    "depends_on": {"minio-init": {"condition": "service_completed_successfully"}},
                    "healthcheck": {
                        "test": ["CMD", "test", "-f", "/state/ready"],
                        "interval": "1s",
                        "timeout": "1s",
                        "retries": 10,
                    },
                },
            },
            "volumes": {"state": {}},
        }
        path = directory / "compose.json"
        path.write_text(json.dumps(config))
        compose = ["docker", "compose", "-p", project, "-f", str(path)]
        try:
            subprocess.run([*compose, "pull"], check=True, timeout=120)
            result = subprocess.run(activation_command(compose, config), timeout=120)
            if (result.returncode == 0) != (exit_code == 0):
                raise RuntimeError(f"Unexpected activation result for initializer exit {exit_code}")
            container = subprocess.check_output(
                [*compose, "ps", "-a", "-q", "minio-init"],
                text=True,
            ).strip()
            actual = subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    container,
                    "--format",
                    "{{.State.ExitCode}}",
                ],
                text=True,
            ).strip()
            if actual != str(exit_code):
                raise RuntimeError("Initializer exit code was not preserved")
            if exit_code == 0:
                subprocess.run(activation_command(compose, config), check=True, timeout=120)
                print("Successful initializer and repeated activation: OK", flush=True)
            else:
                print("Failed initializer blocks activation: OK", flush=True)
        finally:
            # Only this random, disposable project's volumes may be removed.
            subprocess.run([*compose, "down", "--volumes"], check=True, timeout=60)


if __name__ == "__main__":
    subprocess.run(["docker", "compose", "version"], check=True)
    check(0)
    check(7)
