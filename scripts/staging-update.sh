#!/usr/bin/env bash
# staging-update.sh — Updates the Acessilia Toolbox staging container and its
# sidecars via GitHub API.
#
# Checks the SHA of the latest commit on the tracked branch via GitHub API and
# confirms that the corresponding images (sha-<7> tag) of the toolbox and of
# every sidecar it runs HAVE BEEN PUBLISHED on GHCR before updating them
# together. Prevents updating to a commit whose build is still running or has
# failed, and the API running against sidecars of another commit.
#
# On the staging server it runs from the clone through scripts/ops.sh
# (`auto-update start`, which the timer installed by `auto-update setup`
# calls); production-update.sh reuses it with production defaults.
#
# Prerequisites: Docker + Docker Compose, jq, and a .env in the deployment
# directory (GHCR_TOKEN raises the GitHub API rate limit).
#
# Tracked branch (Bug Hunting/Squashing):
#   "develop" by default. During BHS `ops.sh track release/x.y.z` sets
#   TRACK_BRANCH in .env; `ops.sh track develop` removes it again.

set -euo pipefail

# ──────────────────────────────────────────────
# Detect the staging directory (where .env and docker-compose.staging.yml live)
# ──────────────────────────────────────────────
# Supported layouts:
#   A) Staging server: /opt/acessilia-toolbox/staging/
#   B) Cloned repo: <repo>/scripts/staging-update.sh -> <repo>/
# Priority: 1. STAGING_DIR/DEPLOY_DIR (env)  2. known server layouts  3. script parent
if [ -n "${STAGING_DIR:-}" ]; then
  STAGING_DIR="$STAGING_DIR"
elif [ -n "${DEPLOY_DIR:-}" ]; then
  STAGING_DIR="$DEPLOY_DIR"
elif [ -d /opt/a9a-toolbox/staging ]; then
  STAGING_DIR="/opt/a9a-toolbox/staging"
elif [ -d /opt/acessilia-toolbox/staging ]; then
  STAGING_DIR="/opt/acessilia-toolbox/staging"
else
  STAGING_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$STAGING_DIR"

# Overridable so production-update.sh can reuse this script: it exports
# COMPOSE_FILE and CONTAINER_NAME for its own deployment.
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.staging.yml}"
CONTAINER_NAME="${CONTAINER_NAME:-acessilia-toolbox-staging}"
GITHUB_REPO="A11yDevs/acessilia-toolbox"

# Sidecars published by the Sidecars workflow (compose service names). They
# are updated with the toolbox so the API never runs against an older sidecar.
SIDECAR_SERVICES=(mineru-serve audiveris-serve nougat-serve rapid-latex-ocr pp-formulanet rapidocr-orient pdf-ua-render verapdf-validate math-speech)
# Sidecars that start only under a Compose profile (#108): audiveris-serve
# with "music", nougat-serve with "nougat" (COMPOSE_PROFILES in .env).
OPTIONAL_SIDECARS=(audiveris-serve nougat-serve)
CI_IMAGE_PREFIX="ghcr.io/a11ydevs/acessilia-toolbox-"

# Cache and status live in a local directory
CACHE_DIR="$STAGING_DIR/var/data"
CACHE_FILE="${STAGING_UPDATE_CACHE:-$CACHE_DIR/.last_sha}"
# Fingerprint of the resolved Compose config last applied: a change of
# profiles, provider file or other settings is a deployment change even when
# the commit is the same (#108).
CONFIG_FILE="${STAGING_CONFIG_CACHE:-$CACHE_DIR/.last_config}"
STATUS_FILE="${STAGING_STATUS_FILE:-$CACHE_DIR/staging-status.json}"
mkdir -p "$CACHE_DIR"

# Run one update at a time: a manual `ops.sh auto-update start` during the
# timer's run would have two `compose up` calls recreating the same containers.
# The MinerU image alone can take minutes to pull, so an overlap is plausible.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$CACHE_DIR/.update.lock"
  if ! flock -n 9; then
    echo "[staging-update] ⏭️  Another update is already running. Skipping."
    exit 0
  fi
fi

# ──────────────────────────────────────────────
# Status helpers
# ──────────────────────────────────────────────
_write_status() {
  # $1 = latest_sha, $2 = running_sha, $3 = last_update (ISO) ou vazio
  local latest_sha="$1" running_sha="$2" last_update="$3"
  local now
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  mkdir -p "$(dirname "$STATUS_FILE")"
  jq -n \
    --arg latest "$latest_sha" \
    --arg running "$running_sha" \
    --arg check "$now" \
    --arg update "$last_update" \
    '{latest_sha: $latest, running_sha: $running, last_check: $check, last_update: $update}' \
    > "$STATUS_FILE"
}

# Prints "<service> <image>" for each sidecar this deployment runs (it has a
# container) from a CI-published image. A sidecar whose *_IMAGE points at a
# local build, or that was never started, is left alone.
_managed_sidecars() {
  local existing svc image
  existing=$(docker compose -f "$COMPOSE_FILE" ps -a --services 2>/dev/null || true)
  for svc in "${SIDECAR_SERVICES[@]}"; do
    grep -qx "$svc" <<<"$existing" || continue
    image=$(jq -r --arg s "$svc" '.services[$s].image // empty' <<<"$COMPOSE_CONFIG")
    [[ "$image" == "$CI_IMAGE_PREFIX"* ]] || continue
    echo "$svc $image"
  done
}

# Removes containers left behind when a previous `compose up` was interrupted.
# Compose creates the replacement container as <old-container-id[:12]>_<name>
# and only afterwards stops/removes the old one and renames the new one. If
# the process dies in between (a pull of the MinerU image can take minutes),
# the next `up` reuses that temporary name and fails with "container name is
# already in use". Deleting the leftovers is safe: the image is already pulled,
# so compose creates the containers again on the `up` that follows.
_clear_interrupted_recreations() {
  local project svc name id cname
  local -a stale
  project=$(jq -r '.name // empty' <<<"$COMPOSE_CONFIG")
  [ -n "$project" ] || return 0
  for svc in toolbox "${SIDECARS[@]}"; do
    name=$(jq -r --arg s "$svc" '.services[$s].container_name // empty' <<<"$COMPOSE_CONFIG")
    [ -n "$name" ] || continue
    stale=()
    while read -r id cname; do
      [ -n "$id" ] || continue
      case "$cname" in
        [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]_"$name")
          stale+=("$id") ;;
      esac
    done < <(docker ps -a \
      --filter "label=com.docker.compose.project=$project" \
      --filter "label=com.docker.compose.service=$svc" \
      --format '{{.ID}} {{.Names}}' 2>/dev/null || true)
    [ "${#stale[@]}" -gt 0 ] || continue
    echo "[staging-update] 🧹 Removing container left by an interrupted recreation of $svc: ${stale[*]}"
    docker rm -f "${stale[@]}" >/dev/null
  done
}

# Removes the container of an optional sidecar whose Compose profile is off.
# Compose leaves such a service out of its config, so it is neither pulled nor
# restarted, but a container created before the profile existed (or while it
# was on) keeps running an ever older image. Its volumes, such as downloaded
# models, are kept, so turning the profile on again is quick. Called only
# after a successful update: by then the toolbox runs with the provider file
# of the same config, so it no longer announces the sidecar being removed.
_retire_disabled_optional_sidecars() {
  local project svc id
  local -a ids
  project=$(jq -r '.name // empty' <<<"$COMPOSE_CONFIG")
  [ -n "$project" ] || return 0
  for svc in "${OPTIONAL_SIDECARS[@]}"; do
    if jq -e --arg s "$svc" '.services | has($s)' <<<"$COMPOSE_CONFIG" >/dev/null; then
      continue
    fi
    ids=()
    while read -r id; do
      [ -n "$id" ] && ids+=("$id")
    done < <(docker ps -a -q \
      --filter "label=com.docker.compose.project=$project" \
      --filter "label=com.docker.compose.service=$svc")
    [ "${#ids[@]}" -gt 0 ] || continue
    echo "[staging-update] 💤 $svc is off (its Compose profile is not enabled); removing its container"
    docker rm -f "${ids[@]}" >/dev/null
  done
}

# sha256 of the resolved Compose config, without writing the config anywhere.
_config_fingerprint() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$COMPOSE_CONFIG" | sha256sum | cut -d' ' -f1
  else
    printf '%s' "$COMPOSE_CONFIG" | shasum -a 256 | cut -d' ' -f1
  fi
}

# Pulls and restarts the toolbox together with the managed sidecars.
# Each step returns explicitly: callers run it as `_update_services || ...`,
# and bash ignores set -e inside a function called that way, so a failed pull
# would otherwise fall through to `up` and count as a successful update.
_update_services() {
  docker compose -f "$COMPOSE_FILE" pull toolbox "${SIDECARS[@]}" || return 1
  _clear_interrupted_recreations || return 1
  docker compose -f "$COMPOSE_FILE" up -d --no-deps toolbox "${SIDECARS[@]}" || return 1
}

# ──────────────────────────────────────────────
# 0. Load settings from .env
# ──────────────────────────────────────────────
# .env is always loaded, so TRACK_BRANCH set there for BHS applies even when
# GHCR_TOKEN already comes from the environment. Variables already exported
# (GHCR_TOKEN, TRACK_BRANCH) win over .env.
ENV_GHCR_TOKEN="${GHCR_TOKEN:-}"
ENV_TRACK_BRANCH="${TRACK_BRANCH:-}"

if [ -f "$STAGING_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$STAGING_DIR/.env"
  set +a
fi

if [ -n "$ENV_GHCR_TOKEN" ]; then
  GHCR_TOKEN="$ENV_GHCR_TOKEN"
fi

if [ -n "$ENV_TRACK_BRANCH" ]; then
  TRACK_BRANCH="$ENV_TRACK_BRANCH"
fi

# Tracked branch/tag: TRACK_BRANCH (env or .env) wins, then the caller's
# DEFAULT_TRACK_BRANCH (production passes "main"), then "develop".
GITHUB_BRANCH="${TRACK_BRANCH:-${DEFAULT_TRACK_BRANCH:-develop}}"

# Docker tags don't accept "/" (e.g. release/0.1.0 -> release-0.1.0)
TRACK_TAG="${GITHUB_BRANCH//\//-}"
IMAGE_TAG="ghcr.io/a11ydevs/acessilia-toolbox:${TRACK_TAG}"
export TRACK_TAG

COMPOSE_CONFIG=$(docker compose -f "$COMPOSE_FILE" config --format json)
SIDECARS=()
SIDECAR_IMAGES=()
while read -r svc image; do
  [ -n "$svc" ] || continue
  SIDECARS+=("$svc")
  SIDECAR_IMAGES+=("$image")
done < <(_managed_sidecars)
echo "[staging-update] Managed sidecars: ${SIDECARS[*]:-none}"
CONFIG_FINGERPRINT=$(_config_fingerprint)
CONFIG_CHANGED=1
if [ -f "$CONFIG_FILE" ] && [ "$(cat "$CONFIG_FILE")" = "$CONFIG_FINGERPRINT" ]; then
  CONFIG_CHANGED=0
fi

# ──────────────────────────────────────────────
# 1. Check SHA of latest commit via GitHub API
# ──────────────────────────────────────────────
AUTH_HEADER=()
if [ -n "${GHCR_TOKEN:-}" ]; then
  AUTH_HEADER=(-H "Authorization: token $GHCR_TOKEN")
fi

# A failed request must reach the fallback below instead of aborting under
# set -e.
if ! LATEST_SHA=$(curl -fsS \
  "${AUTH_HEADER[@]}" \
  "https://api.github.com/repos/$GITHUB_REPO/commits/$GITHUB_BRANCH" \
  | jq -r '.sha'); then
  LATEST_SHA=""
fi

# If SHA could not be obtained, pull directly (safe fallback)
if [ -z "$LATEST_SHA" ] || [ "$LATEST_SHA" = "null" ]; then
  echo "[staging-update] ⚠️  Failed to query GitHub API. Pulling directly..."
  _update_services || {
    echo "[staging-update] ❌ Failed to pull or restart $IMAGE_TAG and its sidecars"
    exit 1
  }
  _retire_disabled_optional_sidecars
  echo "$CONFIG_FINGERPRINT" > "$CONFIG_FILE"
  docker image prune -f
  _write_status "" "" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[staging-update] Container updated (fallback)."
  exit 0
fi

# Compare with the branch and SHA of the last execution. The branch is part of
# the key because a newly tracked branch (e.g. the BHS cut) can point at the
# same commit: comparing only the SHA kept the containers on the previous
# branch's images until the next commit.
CACHE_KEY="$GITHUB_BRANCH $LATEST_SHA"
if [ -f "$CACHE_FILE" ]; then
  CACHED_KEY=$(cat "$CACHE_FILE")
  if [ "$CACHED_KEY" = "$CACHE_KEY" ] && [ "$CONFIG_CHANGED" = 0 ]; then
    # Nothing to update means the cached commit is the one running.
    _write_status "$LATEST_SHA" "$LATEST_SHA" ""
    echo "[staging-update] ✅ No new commits on $GITHUB_REPO/$GITHUB_BRANCH. Skipping."
    exit 0
  fi
fi

# ──────────────────────────────────────────────
# 2. Confirm that the commit image is already on GHCR
# ──────────────────────────────────────────────
SHA7="${LATEST_SHA:0:7}"
SHA_TAG="ghcr.io/a11ydevs/acessilia-toolbox:sha-$SHA7"

if docker manifest inspect "$SHA_TAG" >/dev/null 2>&1; then
  echo "[staging-update] ✅ Image sha-$SHA7 already published on GHCR."
else
  _write_status "$LATEST_SHA" "" ""
  echo "[staging-update] ⏳ Image sha-$SHA7 not yet published on GHCR (build in progress?). Waiting for next check."
  exit 0
fi

# The sidecars of the same commit must be published too; otherwise the API
# would start against sidecars built from an older commit.
for image in "${SIDECAR_IMAGES[@]}"; do
  sidecar_sha_tag="${image%:*}:sha-$SHA7"
  if ! docker manifest inspect "$sidecar_sha_tag" >/dev/null 2>&1; then
    _write_status "$LATEST_SHA" "" ""
    echo "[staging-update] ⏳ Sidecar image $sidecar_sha_tag not yet published on GHCR. Waiting for next check."
    exit 0
  fi
done

# ──────────────────────────────────────────────
# 3. SHA changed and image published → update
# ──────────────────────────────────────────────
if [ -f "$CACHE_FILE" ] && [ "$(cat "$CACHE_FILE")" = "$CACHE_KEY" ]; then
  echo "[staging-update] 🔄 Compose configuration changed at $SHA7. Updating..."
else
  echo "[staging-update] 🔄 New commit detected: $SHA7. Updating..."
fi

echo "[staging-update] 🚀 Pulling and restarting toolbox ${SIDECARS[*]}..."
_update_services || {
  echo "[staging-update] ❌ Failed to pull or restart $IMAGE_TAG and its sidecars"
  exit 1
}

# Only after a successful restart (set -e aborts before on failure): the
# toolbox now runs this config, so retiring disabled sidecars is safe.
_retire_disabled_optional_sidecars
echo "$CACHE_KEY" > "$CACHE_FILE"
echo "$CONFIG_FINGERPRINT" > "$CONFIG_FILE"

docker image prune -f

_write_status "$LATEST_SHA" "$LATEST_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "[staging-update] ✅ Container $CONTAINER_NAME updated successfully."