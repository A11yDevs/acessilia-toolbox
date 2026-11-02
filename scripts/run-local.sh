#!/usr/bin/env bash
# Run Acessilia Toolbox locally with hot-reload (uvicorn --reload).
# Usage: ./scripts/run-local.sh
# Requires: docling-serve running via Docker (docker compose up -d docling-serve)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Load local environment variables (skip comments and blank lines)
if [[ ! -f .env.local ]]; then
    echo "❌ .env.local not found. Create it from .env.local.example:"
    echo "   cp .env.local.example .env.local"
    echo "   # Then edit .env.local with real credentials"
    exit 1
fi

while IFS='=' read -r key value; do
    if [[ -n "$key" ]]; then
        export "$key=$value"
    fi
done < <(grep -v '^\s*#' .env.local | grep -v '^\s*$')

echo "=== Acessilia Toolbox (local dev) ==="
echo "DOCLING_SERVE_URL=$DOCLING_SERVE_URL"
echo "Port: ${TOOLBOX_PORT:-8002}"
echo "Reload: enabled"
echo ""

uvicorn acessilia_toolbox.api.app:create_app \
    --factory \
    --host "${TOOLBOX_HOST:-0.0.0.0}" \
    --port "${TOOLBOX_PORT:-8002}" \
    --reload