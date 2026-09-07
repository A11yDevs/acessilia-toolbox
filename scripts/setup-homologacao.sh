#!/usr/bin/env bash
# setup-homologacao.sh — Prepares the Acessilia Toolbox staging server
# with systemd timer for automatic updates.
#
# Usage:
#   ./scripts/setup-homologacao.sh                              # Interactive mode
#   ./scripts/setup-homologacao.sh --github-user <user> --token <token>  # Non-interactive mode
#
# Environment variables (alternative to arguments):
#   GITHUB_USER=<user> GHCR_TOKEN=<token> ./scripts/setup-homologacao.sh
#
# Prerequisites:
#   - Docker + Docker Compose installed
#   - jq installed (sudo apt install jq)
#
# This script:
#   1. Configures GHCR authentication
#   2. Creates .env from .env.example if it doesn't exist
#   3. Starts containers with docker compose -f docker-compose.staging.yml
#   4. Installs the systemd user timer (systemctl --user) for automatic updates
#      via GitHub API

set -euo pipefail
cd "$(dirname "$0")/.."

# ──────────────────────────────────────────────
# Parse CLI arguments
# ──────────────────────────────────────────────
GITHUB_USER="${GITHUB_USER:-}"
GHCR_TOKEN="${GHCR_TOKEN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --github-user) GITHUB_USER="$2"; shift 2 ;;
    --token)       GHCR_TOKEN="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 [--github-user <user>] [--token <token>]"
      echo ""
      echo "Environment variables: GITHUB_USER, GHCR_TOKEN"
      exit 0 ;;
    *) echo "❌ Unknown argument: $1"; exit 1 ;;
  esac
done

echo "=== Acessilia Toolbox Staging Environment Setup ==="
echo ""

# ──────────────────────────────────────────────
# 1. Check dependencies
# ──────────────────────────────────────────────
echo "[1/5] Checking dependencies..."

if ! command -v docker &>/dev/null; then
  echo "❌ Docker not found. Install at: https://docs.docker.com/engine/install/"
  exit 1
fi

if ! docker compose version &>/dev/null; then
  echo "❌ Docker Compose not found."
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "❌ jq not found. Install with: sudo apt install jq"
  exit 1
fi

echo "  ✅ Docker $(docker --version)"
echo "  ✅ Compose $(docker compose version --short)"
echo "  ✅ jq $(jq --version)"

# ──────────────────────────────────────────────
# 2. GHCR authentication
# ──────────────────────────────────────────────
echo ""
echo "[2/5] Configuring GitHub Container Registry authentication..."

if [ ! -f ~/.docker/config.json ] || ! grep -q 'ghcr.io' ~/.docker/config.json 2>/dev/null; then
  if [ -z "$GITHUB_USER" ]; then
    read -rp "  Your GitHub username: " GITHUB_USER
  fi
  if [ -z "$GHCR_TOKEN" ]; then
    echo "  Create at: https://github.com/settings/tokens/new?scopes=read:packages"
    read -rp "  Paste the token (or leave blank to skip): " GHCR_TOKEN
  fi

  if [ -n "$GHCR_TOKEN" ] && [ -n "$GITHUB_USER" ]; then
    echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GITHUB_USER" --password-stdin
    echo "  ✅ GHCR login configured."
  else
    echo "  ⚠️  Token or username not provided. Run manually:"
    echo "     echo <token> | docker login ghcr.io -u <your-user> --password-stdin"
  fi
else
  echo "  ✅ GHCR already configured."
fi

# ──────────────────────────────────────────────
# 3. .env file
# ──────────────────────────────────────────────
echo ""
echo "[3/5] Checking .env file..."

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "  ✅ .env created from .env.example"
    echo "  ⚠️  Edit .env with the required credentials (MINIO_ACCESS_KEY, MINIO_SECRET_KEY...)"
  else
    echo "  ⚠️  .env.example not found. Create .env manually."
  fi
else
  echo "  ✅ .env already exists."
fi

# ──────────────────────────────────────────────
# 4. Start containers
# ──────────────────────────────────────────────
echo ""
echo "[4/5] Starting containers with the latest image..."

docker compose -f docker-compose.staging.yml pull toolbox
docker compose -f docker-compose.staging.yml up -d

# ──────────────────────────────────────────────
# 5. Configure automatic update via systemd user timer
# ──────────────────────────────────────────────
echo ""
echo "[5/5] Configuring automatic update via systemd user timer..."

SCRIPTS_DIR="$(pwd)/scripts"

# Cria o diretório para os scripts
sudo mkdir -p /opt/acessilia-toolbox/scripts
sudo cp "$SCRIPTS_DIR/staging-update.sh" /opt/acessilia-toolbox/scripts/
sudo chmod +x /opt/acessilia-toolbox/scripts/staging-update.sh

# Detect the staging directory (where .env and docker-compose.staging.yml live)
STAGING_DIR="${STAGING_DIR:-}"
if [ -z "$STAGING_DIR" ] && [ -d /opt/acessilia-toolbox/staging ]; then
  STAGING_DIR="/opt/acessilia-toolbox/staging"
fi
if [ -z "$STAGING_DIR" ]; then
  STAGING_DIR="$(pwd)"
fi

# Persist token in staging .env
if [ -n "$GHCR_TOKEN" ]; then
  if [ -f "$STAGING_DIR/.env" ]; then
    if grep -q '^GHCR_TOKEN=' "$STAGING_DIR/.env"; then
      sudo sed -i "s|^GHCR_TOKEN=.*|GHCR_TOKEN=$GHCR_TOKEN|" "$STAGING_DIR/.env"
    else
      echo "GHCR_TOKEN=$GHCR_TOKEN" | sudo tee -a "$STAGING_DIR/.env" > /dev/null
    fi
  else
    echo "GHCR_TOKEN=$GHCR_TOKEN" | sudo tee "$STAGING_DIR/.env" > /dev/null
  fi
  sudo chmod 600 "$STAGING_DIR/.env"
fi

# User timer prerequisites: linger + docker group
if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
  echo "  ⚠️  Enabling linger for $USER (user timer survives logout)..."
  sudo loginctl enable-linger "$USER"
fi
if ! id -nG | tr ' ' '\n' | grep -qx docker; then
  echo "  ⚠️  Adding $USER to docker group (user timer accesses daemon)..."
  sudo usermod -aG docker "$USER"
  echo "  ⚠️  Re-log (logout/login) for docker group to take effect."
fi

# Create user units directory
mkdir -p ~/.config/systemd/user

# Create the service unit (user)
cat > ~/.config/systemd/user/staging-update.service << 'SERVICE'
[Unit]
Description=Update acessilia-toolbox staging container

[Service]
Type=oneshot
ExecStart=/opt/acessilia-toolbox/scripts/staging-update.sh
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
SERVICE

# Create the timer unit (user, every 5 minutes)
cat > ~/.config/systemd/user/staging-update.timer << 'TIMER'
[Unit]
Description=Check acessilia-toolbox staging updates every 5 minutes

[Timer]
OnBootSec=30s
OnUnitActiveSec=300s

[Install]
WantedBy=timers.target
TIMER

systemctl --user daemon-reload
systemctl --user enable --now staging-update.timer

echo "  ✅ Systemd user timer installed and active."
echo "  ⏱   Checks every 5 minutes via GitHub API for new commits on develop"
echo "  🔑  GHCR token saved in $STAGING_DIR/.env (mode 600)"
echo ""

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Access:"
echo "  Toolbox API: http://localhost:8002"
echo "  MinIO Console: http://localhost:9001"
echo ""
echo "To view logs:"
echo "  docker compose -f docker-compose.staging.yml logs -f"
echo ""
echo "To stop:"
echo "  docker compose -f docker-compose.staging.yml down"