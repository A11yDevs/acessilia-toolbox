"""scripts/staging-update.sh against stubbed curl and docker commands."""

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

SHA = "1234567890abcdef"

_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STAGING_DIR/docker-args.txt"
case "$*" in
  *"config --format json"*)
    if [ -n "${DOCKER_CONFIG_JSON:-}" ]; then
      printf '%s' "$DOCKER_CONFIG_JSON"
    else
      printf '{"services":{}}'
    fi ;;
  compose*"ps -a --services"*) printf '%b' "${COMPOSE_SERVICES:-}" ;;
  ps*-a*) printf '%b' "${DOCKER_PS:-}" ;;
esac
exit 0
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _staging(tmp_path: Path, dotenv: str, curl: str) -> tuple[Path, dict[str, str]]:
    staging_dir = tmp_path / "staging"
    bin_dir = tmp_path / "bin"
    (staging_dir / "var" / "data").mkdir(parents=True)
    bin_dir.mkdir()
    (staging_dir / "docker-compose.staging.yml").write_text("services: {}\n")
    (staging_dir / ".env").write_text(dotenv)

    script_path = staging_dir / "staging-update.sh"
    shutil.copy(ROOT_DIR / "scripts" / "staging-update.sh", script_path)
    script_path.chmod(0o755)

    _write_executable(bin_dir / "curl", curl)
    _write_executable(bin_dir / "docker", _DOCKER)

    env = os.environ.copy()
    env.update(
        {
            "GHCR_TOKEN": "token-from-environment",
            "PATH": f"{bin_dir}:{env['PATH']}",
            "STAGING_DIR": str(staging_dir),
        }
    )
    env.pop("TRACK_BRANCH", None)
    return staging_dir, env


def _run(staging_dir: Path, env: dict[str, str]) -> str:
    subprocess.run(
        [str(staging_dir / "staging-update.sh")],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return (staging_dir / "docker-args.txt").read_text()


_CURL_OK = f"""#!/usr/bin/env bash
printf '%s\\n' "$*" > "$STAGING_DIR/curl-args.txt"
printf '{{"sha":"{SHA}"}}'
"""


def test_track_branch_from_dotenv_applies_with_token_in_environment(tmp_path):
    staging_dir, env = _staging(tmp_path, "TRACK_BRANCH=release/0.2.0\n", _CURL_OK)

    docker_calls = _run(staging_dir, env)

    assert "/commits/release/0.2.0" in (staging_dir / "curl-args.txt").read_text()
    assert "manifest inspect ghcr.io/a11ydevs/acessilia-toolbox:sha-1234567" in docker_calls
    assert "compose -f docker-compose.staging.yml up -d --no-deps toolbox" in docker_calls


def test_new_track_branch_at_cached_commit_still_updates(tmp_path):
    """At the BHS cut release/x.y.z can point at the commit develop runs."""
    staging_dir, env = _staging(tmp_path, "TRACK_BRANCH=release/0.2.0\n", _CURL_OK)
    cache_file = staging_dir / "var" / "data" / ".last_sha"
    cache_file.write_text(f"develop {SHA}")

    docker_calls = _run(staging_dir, env)

    assert "compose -f docker-compose.staging.yml pull toolbox" in docker_calls
    assert cache_file.read_text().strip() == f"release/0.2.0 {SHA}"


def test_same_branch_at_cached_commit_skips(tmp_path):
    staging_dir, env = _staging(tmp_path, "TRACK_BRANCH=release/0.2.0\n", _CURL_OK)
    (staging_dir / "var" / "data" / ".last_sha").write_text(f"release/0.2.0 {SHA}")

    docker_calls = _run(staging_dir, env)

    assert "pull" not in docker_calls


def test_failed_github_request_falls_back_to_pulling(tmp_path):
    staging_dir, env = _staging(
        tmp_path, "TRACK_BRANCH=release/0.2.0\n", "#!/usr/bin/env bash\nexit 22\n"
    )

    docker_calls = _run(staging_dir, env)

    assert "compose -f docker-compose.staging.yml pull toolbox" in docker_calls
    assert "compose -f docker-compose.staging.yml up -d --no-deps toolbox" in docker_calls


def _sidecar_config() -> str:
    return json.dumps(
        {
            "name": "acessilia-toolbox-staging",
            "services": {
                "mineru-serve": {
                    "image": "ghcr.io/a11ydevs/acessilia-toolbox-mineru:release-0.2.0",
                    "container_name": "mineru-serve-staging",
                }
            },
        }
    )


def test_removes_container_left_by_interrupted_compose_recreate(tmp_path):
    """Compose renames the old container to <id>_<name> before recreating it.

    If a previous `up` was interrupted in between, that renamed container stays
    behind and the next `up` fails with "container name is already in use".
    """
    staging_dir, env = _staging(tmp_path, "TRACK_BRANCH=release/0.2.0\n", _CURL_OK)
    env.update(
        {
            "DOCKER_CONFIG_JSON": _sidecar_config(),
            "COMPOSE_SERVICES": "mineru-serve\n",
            "DOCKER_PS": (
                "af9061dc09c4 e84cd08e46e5_mineru-serve-staging\n"
                "deadbeef0000 mineru-serve-staging\n"
            ),
        }
    )

    docker_calls = _run(staging_dir, env)

    assert "rm -f af9061dc09c4" in docker_calls
    assert "rm -f deadbeef0000" not in docker_calls
    assert (
        "compose -f docker-compose.staging.yml up -d --no-deps toolbox mineru-serve"
        in docker_calls
    )
    cache_file = staging_dir / "var" / "data" / ".last_sha"
    assert cache_file.read_text().strip() == f"release/0.2.0 {SHA}"
