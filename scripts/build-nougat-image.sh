#!/usr/bin/env bash
# Build the Nougat Docker image.
#
# Builds a CPU-based image running a lightweight FastAPI service for Nougat
# (facebookresearch/nougat) from docker/nougat-cpu.Dockerfile.
#
# Usage:
#   ./scripts/build-nougat-image.sh            # latest
#   ./scripts/build-nougat-image.sh 0.1.17     # pinned
#
# Requirements: docker.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

NOUGAT_VERSION="${1:-latest}"
IMAGE_TAG="nougat:${NOUGAT_VERSION}"
DOCKERFILE="${PROJECT_DIR}/docker/nougat-cpu.Dockerfile"

echo "=== Build Nougat image ==="
echo "Version: ${NOUGAT_VERSION}"
echo "Image:   ${IMAGE_TAG}"
echo ""

command -v docker >/dev/null 2>&1 || { echo "❌ docker is required"; exit 1; }
docker info >/dev/null 2>&1 || { echo "❌ docker daemon is not running"; exit 1; }

[[ -f "${DOCKERFILE}" ]] || { echo "❌ ${DOCKERFILE} not found"; exit 1; }

echo "→ Building image from ${DOCKERFILE}"
docker build -t "${IMAGE_TAG}" -f "${DOCKERFILE}" "${PROJECT_DIR}"

echo ""
echo "✅ Built ${IMAGE_TAG}"
echo ""
echo "Next steps:"
echo "  docker compose up -d nougat-serve"
