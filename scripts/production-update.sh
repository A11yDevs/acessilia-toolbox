#!/usr/bin/env bash
# production-update.sh — Update the production container tracking main.
#
# Reuses the staging update mechanism but with production defaults:
# /opt/a9a-toolbox/production, docker-compose.staging.yml,
# container acessilia-toolbox-staging and branch main.
#
# Usage:
#   ./scripts/production-update.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export DEPLOY_DIR="${PRODUCTION_DIR:-${DEPLOY_DIR:-/opt/a9a-toolbox/production}}"
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.staging.yml}"
export CONTAINER_NAME="${CONTAINER_NAME:-acessilia-toolbox-production}"
export DEPLOY_ENV="${DEPLOY_ENV:-production}"
export DEFAULT_TRACK_BRANCH="${DEFAULT_TRACK_BRANCH:-main}"
export STAGING_UPDATE_CACHE="${STAGING_UPDATE_CACHE:-$DEPLOY_DIR/var/data/.last_production_sha}"

exec "$SCRIPT_DIR/staging-update.sh"