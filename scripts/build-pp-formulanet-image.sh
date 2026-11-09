#!/usr/bin/env bash
# Build the PP-FormulaNet_plus-M math formula image.
#
# Builds a CPU-based image running PaddleOCR's formula recognition module from
# docker/pp-formulanet.Dockerfile. The Toolbox reaches it through
# PP_FORMULANET_URL and uses it as the `pp-formulanet` provider for
# `math.recognize`.
#
# Usage:
#   ./scripts/build-pp-formulanet-image.sh            # latest
#   ./scripts/build-pp-formulanet-image.sh 0.1.0      # pinned
#
# Requirements: docker and network access (the model weights are fetched at
# build time).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PP_FORMULANET_VERSION="${1:-latest}"
IMAGE_TAG="pp-formulanet:${PP_FORMULANET_VERSION}"
DOCKERFILE="${PROJECT_DIR}/docker/pp-formulanet.Dockerfile"

echo "=== Build PP-FormulaNet image ==="
echo "Version: $PP_FORMULANET_VERSION"
echo "Image:   $IMAGE_TAG"
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
echo "  PP_FORMULANET_IMAGE=${IMAGE_TAG} docker compose up -d pp-formulanet"
echo "  Set TOOLBOX_MATH_RECOGNIZE_PROVIDER=pp-formulanet in the core."
