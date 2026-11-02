#!/usr/bin/env bash
# Build the MinerU Docker image.
#
# Two variants:
#   cpu (default) — lightweight CPU-only image (~4-6 GB) built from
#                   docker/mineru-cpu.Dockerfile in this repo. Installs only
#                   the `pipeline` backend. Use for dev and CPU staging.
#   gpu           — official vllm-based image (~15-20 GB) built from the
#                   MinerU repo's docker/global/Dockerfile. Requires a Linux
#                   host with NVIDIA GPU and ~20 GB free disk.
#
# Usage:
#   ./scripts/build-mineru-image.sh                 # CPU variant, latest
#   ./scripts/build-mineru-image.sh 2.1.11          # CPU variant, pinned
#   MINERU_REPAIR=1 ./scripts/build-mineru-image.sh # patch existing CPU image
#   MINERU_VARIANT=gpu ./scripts/build-mineru-image.sh   # GPU variant
#
# Requirements: git, docker. The CPU variant builds anywhere; the GPU variant
# needs a Linux host (Docker on macOS cannot access GPU acceleration).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MINERU_VERSION="${1:-latest}"
MINERU_VARIANT="${MINERU_VARIANT:-cpu}"
MINERU_REPAIR="${MINERU_REPAIR:-0}"
MINERU_REPO_URL="${MINERU_REPO_URL:-https://github.com/opendatalab/MinerU.git}"
WORK_DIR="${MINERU_WORK_DIR:-/tmp/mineru-build}"
IMAGE_TAG="mineru:${MINERU_VERSION}"

echo "=== Build MinerU image (${MINERU_VARIANT}) ==="
echo "Version: ${MINERU_VERSION}"
echo "Image:   ${IMAGE_TAG}"
echo ""

# ── Preflight ────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || { echo "❌ git is required"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "❌ docker is required"; exit 1; }
docker info >/dev/null 2>&1 || { echo "❌ docker daemon is not running"; exit 1; }

# ── CPU variant: build from our own Dockerfile, no clone needed ──────
if [[ "${MINERU_VARIANT}" == "cpu" ]]; then
    if [[ "${MINERU_REPAIR}" == "1" ]]; then
        docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1 || {
            echo "❌ ${IMAGE_TAG} does not exist; run a full CPU build first"
            exit 1
        }
        BASE_TAG="mineru:cpu-base"
        echo "→ Tagging ${IMAGE_TAG} as ${BASE_TAG}"
        docker tag "${IMAGE_TAG}" "${BASE_TAG}"
        echo "→ Applying the low-disk repair layer"
        docker build \
            --build-arg "MINERU_BASE_IMAGE=${BASE_TAG}" \
            -t "${IMAGE_TAG}" \
            -f "${PROJECT_DIR}/docker/mineru-cpu-repair.Dockerfile" \
            "${PROJECT_DIR}/docker"
        echo "✅ Repaired ${IMAGE_TAG}"
        exit 0
    fi

    DOCKERFILE="${PROJECT_DIR}/docker/mineru-cpu.Dockerfile"
    [[ -f "${DOCKERFILE}" ]] || { echo "❌ ${DOCKERFILE} not found"; exit 1; }

    echo "→ Building CPU-only image from ${DOCKERFILE}"
    docker build -t "${IMAGE_TAG}" -f "${DOCKERFILE}" "${PROJECT_DIR}/docker"

    echo ""
    echo "✅ Built ${IMAGE_TAG} (CPU-only)"
    echo ""
    echo "Next steps:"
    echo "  ./scripts/run-mineru.sh                 # start the container"
    exit 0
fi

# ── GPU variant: clone the official repo and build its Dockerfile ────
echo "Workdir: ${WORK_DIR}"
if [[ -d "${WORK_DIR}/.git" ]]; then
    echo "→ Updating existing clone at ${WORK_DIR}"
    git -C "${WORK_DIR}" fetch --tags --quiet
else
    echo "→ Cloning ${MINERU_REPO_URL}"
    git clone --depth 1 --branch master "${MINERU_REPO_URL}" "${WORK_DIR}"
fi

if [[ "${MINERU_VERSION}" != "latest" ]]; then
    echo "→ Checking out tag mineru-${MINERU_VERSION}-released"
    git -C "${WORK_DIR}" checkout "mineru-${MINERU_VERSION}-released"
fi

DOCKERFILE="${WORK_DIR}/docker/global/Dockerfile"

echo "→ Building ${IMAGE_TAG} (downloads ~10 GB of layers; grab a coffee)"
docker build -t "${IMAGE_TAG}" -f "${DOCKERFILE}" "${WORK_DIR}"

echo ""
echo "✅ Built ${IMAGE_TAG} (GPU/vllm variant)"
echo ""
echo "Next steps:"
echo "  ./scripts/run-mineru.sh                 # start the container"
echo "  docker compose -f docker-compose.staging.yml up -d mineru-serve"