"""scripts/staging-update.sh against stubbed curl and docker commands."""

import hashlib
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
  compose*" pull "*) [ -z "${DOCKER_FAIL_PULL:-}" ] || exit 1 ;;
  "manifest inspect "*) [ -z "${DOCKER_NO_MANIFEST:-}" ] || exit 1 ;;
  *"config --format json"*)
    if [ -n "${DOCKER_CONFIG_JSON:-}" ]; then
      printf '%s' "$DOCKER_CONFIG_JSON"
    else
      printf '{"services":{}}'
    fi ;;
  compose*"ps -a --services"*) printf '%b' "${COMPOSE_SERVICES:-}" ;;
  "ps -a -q "*"com.docker.compose.service="*)
    args="$*"
    svc="${args##*com.docker.compose.service=}"
    var="SERVICE_IDS_${svc//-/_}"
    printf '%b' "${!var:-}" ;;
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


def _fingerprint(config_json: str = '{"services":{}}') -> str:
    """What the updater stores for a config: sha256 of the resolved JSON."""
    return hashlib.sha256(config_json.encode()).hexdigest()


def test_same_branch_at_cached_commit_skips(tmp_path):
    staging_dir, env = _staging(tmp_path, "TRACK_BRANCH=release/0.2.0\n", _CURL_OK)
    (staging_dir / "var" / "data" / ".last_sha").write_text(f"release/0.2.0 {SHA}")
    (staging_dir / "var" / "data" / ".last_config").write_text(_fingerprint() + "\n")

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


def _config_with(*services: str) -> str:
    """A staging compose config listing ``services`` (others have their profile off)."""
    images = {
        "mineru-serve": "ghcr.io/a11ydevs/acessilia-toolbox-mineru:develop",
        "audiveris-serve": "ghcr.io/a11ydevs/acessilia-toolbox-audiveris-serve:develop",
        "nougat-serve": "ghcr.io/a11ydevs/acessilia-toolbox-nougat:develop",
    }
    return json.dumps(
        {
            "name": "acessilia-toolbox-staging",
            "services": {
                service: {"image": images[service], "container_name": f"{service}-staging"}
                for service in services
            },
        }
    )


def _optional_off(tmp_path, **extra):
    """nougat-serve has a container from before; its profile is now off."""
    staging_dir, env = _staging(tmp_path, "", _CURL_OK)
    env.update(
        {
            "DOCKER_CONFIG_JSON": _config_with("mineru-serve"),
            "COMPOSE_SERVICES": "mineru-serve\nnougat-serve\n",
            "SERVICE_IDS_nougat_serve": "abc123\n",
            **extra,
        }
    )
    return staging_dir, env


def _lines(docker_calls: str) -> list[str]:
    return docker_calls.splitlines()


def test_removes_a_disabled_optional_sidecar_after_the_toolbox_is_updated(tmp_path):
    """#108: retire only once the toolbox runs the config without that provider."""
    staging_dir, env = _optional_off(tmp_path)

    lines = _lines(_run(staging_dir, env))

    up = next(i for i, line in enumerate(lines) if "up -d --no-deps" in line)
    assert "nougat-serve" not in lines[up]
    assert lines.index("rm -f abc123") > up
    config_cache = staging_dir / "var" / "data" / ".last_config"
    assert config_cache.read_text().strip() == _fingerprint(env["DOCKER_CONFIG_JSON"])


def test_keeps_the_sidecar_while_the_toolbox_image_is_not_published(tmp_path):
    """The old API may still announce the provider: nothing changes yet."""
    staging_dir, env = _optional_off(tmp_path, DOCKER_NO_MANIFEST="1")

    docker_calls = _run(staging_dir, env)

    assert "up -d" not in docker_calls
    assert "rm -f abc123" not in docker_calls


def test_keeps_the_sidecar_when_the_update_fails(tmp_path):
    staging_dir, env = _optional_off(tmp_path, DOCKER_FAIL_PULL="1")

    result = subprocess.run(
        [str(staging_dir / "staging-update.sh")], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "rm -f abc123" not in (staging_dir / "docker-args.txt").read_text()
    assert not (staging_dir / "var" / "data" / ".last_config").exists()


def test_a_config_change_at_the_same_commit_is_applied_before_retiring(tmp_path):
    """Profiles turned off without a new commit still recreate the toolbox first."""
    staging_dir, env = _optional_off(tmp_path)
    data = staging_dir / "var" / "data"
    (data / ".last_sha").write_text(f"develop {SHA}")
    (data / ".last_config").write_text(_fingerprint('{"previous":"config"}') + "\n")

    lines = _lines(_run(staging_dir, env))

    up = next(i for i, line in enumerate(lines) if "up -d --no-deps" in line)
    assert lines.index("rm -f abc123") > up


def test_same_commit_and_config_change_nothing(tmp_path):
    staging_dir, env = _optional_off(tmp_path)
    data = staging_dir / "var" / "data"
    (data / ".last_sha").write_text(f"develop {SHA}")
    (data / ".last_config").write_text(_fingerprint(env["DOCKER_CONFIG_JSON"]) + "\n")

    docker_calls = _run(staging_dir, env)

    assert "pull" not in docker_calls
    assert "rm -f abc123" not in docker_calls


def test_keeps_an_optional_sidecar_whose_profile_is_on(tmp_path):
    staging_dir, env = _staging(tmp_path, "", _CURL_OK)
    env.update(
        {
            "DOCKER_CONFIG_JSON": _config_with("mineru-serve", "nougat-serve"),
            "COMPOSE_SERVICES": "mineru-serve\nnougat-serve\n",
            "SERVICE_IDS_nougat_serve": "abc123\n",
        }
    )

    docker_calls = _run(staging_dir, env)

    assert "rm -f abc123" not in docker_calls
    assert (
        "compose -f docker-compose.staging.yml up -d --no-deps toolbox mineru-serve nougat-serve"
        in docker_calls
    )


def test_never_removes_a_sidecar_that_is_not_optional(tmp_path):
    """Only audiveris-serve and nougat-serve are optional; mineru-serve is left alone."""
    staging_dir, env = _staging(tmp_path, "", _CURL_OK)
    env.update(
        {
            "DOCKER_CONFIG_JSON": _config_with(),
            "SERVICE_IDS_mineru_serve": "def456\n",
        }
    )

    docker_calls = _run(staging_dir, env)

    assert "def456" not in docker_calls.replace("com.docker.compose.service=mineru-serve", "")
    assert "rm -f def456" not in docker_calls
