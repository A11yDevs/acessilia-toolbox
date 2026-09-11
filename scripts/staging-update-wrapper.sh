#!/usr/bin/env bash
# staging-update-wrapper.sh — Wrapper that loads GHCR_TOKEN from .env
# before running staging-update.sh.
#
# Usage:
#   ./scripts/staging-update-wrapper.sh
#
# This wrapper ensures the GHCR token is available even when the
# systemd timer runs without a full login shell.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STAGING_DIR="${STAGING_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Load GHCR_TOKEN from .env if not already set
if [ -z "${GHCR_TOKEN:-}" ]; then
  if [ -f "$STAGING_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$STAGING_DIR/.env"
    set +a
  fi
fi

exec "$SCRIPT_DIR/staging-update.sh"