#!/usr/bin/env bash
# Run the MinerU API container locally (outside Docker Compose).
#
# Starts mineru-api on port 5002 (the port the toolbox expects via
# MINERU_SERVE_URL) with a persistent model cache volume.
#
# Usage:
#   ./scripts/run-mineru.sh              # start (or restart) mineru-serve
#   ./scripts/run-mineru.sh stop         # stop and remove the container
#   ./scripts/run-mineru.sh logs         # tail container logs
#   ./scripts/run-mineru.sh status       # health check
#
# The image must exist first: ./scripts/build-mineru-image.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load .env.local if present (for MINERU_SERVE_PORT etc.)
if [[ -f "${PROJECT_DIR}/.env.local" ]]; then
    while IFS='=' read -r key value; do
        [[ -n "$key" ]] && export "$key=$value"
    done < <(grep -v '^\s*#' "${PROJECT_DIR}/.env.local" | grep -v '^\s*$')
fi

IMAGE="${MINERU_SERVE_IMAGE:-mineru:latest}"
CONTAINER="mineru-serve"
PORT="${MINERU_SERVE_PORT:-5002}"
API_PORT=8000   # mineru-api listens here inside the container
VOLUME="mineru-models"

case "${1:-start}" in
    stop)
        echo "→ Stopping ${CONTAINER}"
        docker rm -f "${CONTAINER}" >/dev/null 2>&1 && echo "✅ Removed" || echo "(not running)"
        exit 0
        ;;
    logs)
        docker logs -f "${CONTAINER}"
        exit 0
        ;;
    status)
        if curl -fsS "http://localhost:${PORT}/openapi.json" >/dev/null 2>&1; then
            echo "✅ ${CONTAINER} healthy at http://localhost:${PORT}"
        else
            echo "❌ ${CONTAINER} not responding at http://localhost:${PORT}"
            exit 1
        fi
        exit 0
        ;;
esac

# ── start ────────────────────────────────────────────────────────────
docker image inspect "${IMAGE}" >/dev/null 2>&1 || {
    echo "❌ Image ${IMAGE} not found. Build it first:"
    echo "   ./scripts/build-mineru-image.sh"
    exit 1
}

# Replace any existing container for a clean start.
docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true

# Named volumes are created as root by Docker. Initialize the cache ownership
# before starting the non-root MinerU process.
docker volume create "${VOLUME}" >/dev/null
docker run --rm \
    --user root \
    --entrypoint sh \
    -v "${VOLUME}:/home/mineru/.cache/modelscope" \
    "${IMAGE}" \
    -c "mkdir -p /home/mineru/.cache/modelscope && chown -R mineru:mineru /home/mineru/.cache/modelscope"

echo "→ Starting ${CONTAINER} (image=${IMAGE}, port=${PORT})"
docker run -d \
    --name "${CONTAINER}" \
    --shm-size 8g \
    -p "${PORT}:${API_PORT}" \
    -v "${VOLUME}:/home/mineru/.cache/modelscope" \
    --restart unless-stopped \
    "${IMAGE}"

echo ""
echo "→ Waiting for the API to come up (first start downloads models, may take minutes)..."
for i in $(seq 1 60); do
    if curl -fsS "http://localhost:${PORT}/openapi.json" >/dev/null 2>&1; then
        echo ""
        echo "✅ MinerU API ready at http://localhost:${PORT}"
        echo "   Toolbox env: MINERU_SERVE_URL=http://localhost:${PORT}"
        exit 0
    fi
    sleep 5
done

echo "❌ Timed out waiting for the API. Check logs:"
echo "   ./scripts/run-mineru.sh logs"
exit 1