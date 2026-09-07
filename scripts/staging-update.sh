#!/usr/bin/env bash
# staging-update.sh — Updates the Acessilia Toolbox staging container
# via GitHub API.
#
# Checks the SHA of the latest commit on the develop branch via GitHub API and
# confirms that the corresponding image (sha-<7> tag) HAS BEEN PUBLISHED on GHCR
# before updating. Prevents updating to a commit whose build is still running
# or has failed.
#
# Usage:
#   ./scripts/staging-update.sh                     # Run once
#   systemctl start staging-update.service           # Run via systemd
#
# Installation as systemd user timer (every 5 min):
#   1. sudo cp scripts/staging-update.sh /opt/acessilia-toolbox/scripts/
#   2. Create ~/.config/systemd/user/staging-update.service
#   3. Create ~/.config/systemd/user/staging-update.timer
#   4. systemctl --user daemon-reload
#   5. systemctl --user enable --now staging-update.timer
#
# Prerequisites:
#   - Docker + Docker Compose installed
#   - jq installed (sudo apt install jq)
#   - docker login ghcr.io configured
#   - GHCR_TOKEN variable set (token with read:packages scope)
#   - Run from the project root directory
#
# Tracked branch (Bug Hunting/Squashing):
#   By default tracks "develop". During the BHS cycle, set TRACK_BRANCH
#   in .env (e.g. TRACK_BRANCH=release/0.1.0) to point staging to the
#   ephemeral release branch; when BHS ends, remove the variable (or revert
#   to "develop") to resume normal tracking.

set -euo pipefail

# ──────────────────────────────────────────────
# Detect the staging directory (where .env and docker-compose.staging.yml live)
# ──────────────────────────────────────────────
# Supported layouts:
#   A) Staging server: /opt/acessilia-toolbox/staging/
#   B) Cloned repo: <repo>/scripts/staging-update.sh -> <repo>/
# Priority: 1. STAGING_DIR (env)  2. /opt/acessilia-toolbox/staging  3. script parent
if [ -n "${STAGING_DIR:-}" ]; then
  STAGING_DIR="$STAGING_DIR"
elif [ -d /opt/acessilia-toolbox/staging ]; then
  STAGING_DIR="/opt/acessilia-toolbox/staging"
else
  STAGING_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$STAGING_DIR"

COMPOSE_FILE="docker-compose.staging.yml"
CONTAINER_NAME="acessilia-toolbox-staging"
GITHUB_REPO="A11yDevs/acessilia-toolbox"

# Cache and status live in a local directory
CACHE_DIR="$STAGING_DIR/var/data"
CACHE_FILE="${STAGING_UPDATE_CACHE:-$CACHE_DIR/.last_sha}"
STATUS_FILE="${STAGING_STATUS_FILE:-$CACHE_DIR/staging-status.json}"
mkdir -p "$CACHE_DIR"

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

# ──────────────────────────────────────────────
# 0. Load GHCR_TOKEN (if not set in environment)
# ──────────────────────────────────────────────
# Possible sources, in order:
#   1. GHCR_TOKEN environment variable
#   2. <STAGING_DIR>/.env file (GHCR_TOKEN=...)
if [ -z "${GHCR_TOKEN:-}" ]; then
  if [ -f "$STAGING_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$STAGING_DIR/.env"
    set +a
  fi
fi

# Tracked branch/tag: "develop" by default, or TRACK_BRANCH (env or .env)
# during the BHS cycle (e.g. release/0.1.0)
GITHUB_BRANCH="${TRACK_BRANCH:-develop}"

# Docker tags don't accept "/" (e.g. release/0.1.0 -> release-0.1.0)
TRACK_TAG="${GITHUB_BRANCH//\//-}"
IMAGE_TAG="ghcr.io/a11ydevs/acessilia-toolbox:${TRACK_TAG}"
export TRACK_TAG

# ──────────────────────────────────────────────
# 1. Check SHA of latest commit via GitHub API
# ──────────────────────────────────────────────
AUTH_HEADER=()
if [ -n "${GHCR_TOKEN:-}" ]; then
  AUTH_HEADER=(-H "Authorization: token $GHCR_TOKEN")
fi

LATEST_SHA=$(curl -fsS \
  "${AUTH_HEADER[@]}" \
  "https://api.github.com/repos/$GITHUB_REPO/commits/$GITHUB_BRANCH" \
  | jq -r '.sha')

# If SHA could not be obtained, pull directly (safe fallback)
if [ -z "$LATEST_SHA" ] || [ "$LATEST_SHA" = "null" ]; then
  echo "[staging-update] ⚠️  Failed to query GitHub API. Pulling directly..."
  docker pull "$IMAGE_TAG" 2>/dev/null || {
    echo "[staging-update] ❌ Failed to pull $IMAGE_TAG"
    exit 1
  }
  docker compose -f "$COMPOSE_FILE" up -d --no-deps toolbox
  docker image prune -f
  _write_status "" "" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[staging-update] Container updated (fallback)."
  exit 0
fi

# Compare with SHA from last execution
if [ -f "$CACHE_FILE" ]; then
  CACHED_SHA=$(cat "$CACHE_FILE")
  if [ "$CACHED_SHA" = "$LATEST_SHA" ]; then
    _write_status "$LATEST_SHA" "" ""
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

# ──────────────────────────────────────────────
# 3. SHA changed and image published → update
# ──────────────────────────────────────────────
echo "[staging-update] 🔄 New commit detected: $SHA7. Updating..."

docker pull "$IMAGE_TAG" 2>/dev/null || {
  echo "[staging-update] ❌ Failed to pull $IMAGE_TAG"
  exit 1
}

echo "[staging-update] 🚀 Restarting container..."
docker compose -f "$COMPOSE_FILE" up -d --no-deps toolbox

# only write cache after successful restart (set -e aborts before on failure)
echo "$LATEST_SHA" > "$CACHE_FILE"

docker image prune -f

_write_status "$LATEST_SHA" "$LATEST_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "[staging-update] ✅ Container $CONTAINER_NAME updated successfully."