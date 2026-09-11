#!/usr/bin/env bash
# update_scripts.sh — Update staging scripts from GitHub (without git clone).
#
# Usage (run on the staging server):
#   ./update_scripts.sh                          # uses tracked branch (TRACK_BRANCH or develop)
#   ./update_scripts.sh release/0.1.0            # force a specific branch/tag
#
# What this script does:
#   1. Downloads staging-update.sh, staging-update-wrapper.sh and
#      docker-compose.staging.yml directly from GitHub (raw.githubusercontent.com),
#      without needing to clone the repository
#   2. Backs up (.bak) existing files
#   3. Installs into /opt/a9a-toolbox/scripts and STAGING_DIR
#
# Prerequisites: curl. Public repository (no token required).

set -euo pipefail

REPO="A11yDevs/acessilia-toolbox"
SCRIPTS_DIR="${SCRIPTS_DIR:-/opt/a9a-toolbox/scripts}"

if [ -n "${STAGING_DIR:-}" ]; then
  STAGING_DIR="$STAGING_DIR"
elif [ -d /opt/a9a-toolbox/staging ]; then
  STAGING_DIR="/opt/a9a-toolbox/staging"
else
  STAGING_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi

# Branch/tag to download: argument > TRACK_BRANCH from .env > develop
REF="${1:-}"
if [ -z "$REF" ] && [ -f "$STAGING_DIR/.env" ]; then
  REF=$(grep -E '^TRACK_BRANCH=' "$STAGING_DIR/.env" | tail -1 | cut -d= -f2-)
fi
REF="${REF:-develop}"

echo "[update-scripts] Downloading files from branch/tag '${REF}'..."

_download() {
  local src="$1" dest="$2"
  local url="https://raw.githubusercontent.com/${REPO}/${REF}/${src}"
  if [ -f "$dest" ]; then
    cp "$dest" "${dest}.bak"
  fi
  curl -fsSL "$url" -o "$dest.tmp"
  mv "$dest.tmp" "$dest"
  echo "  ✅ ${dest}"
}

# Use sudo only if the directory is not writable
mkdir -p "$SCRIPTS_DIR" 2>/dev/null || sudo mkdir -p "$SCRIPTS_DIR"
_download "scripts/staging-update.sh" "$SCRIPTS_DIR/staging-update.sh"
_download "scripts/staging-update-wrapper.sh" "$SCRIPTS_DIR/staging-update-wrapper.sh"
chmod +x "$SCRIPTS_DIR/staging-update.sh" "$SCRIPTS_DIR/staging-update-wrapper.sh"

_download "docker-compose.staging.yml" "$STAGING_DIR/docker-compose.staging.yml"

echo ""
echo "[update-scripts] ✅ Scripts updated. To apply now:"
echo "  systemctl --user start staging-update.service"