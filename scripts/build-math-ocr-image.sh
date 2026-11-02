#!/usr/bin/env bash
# Build the RapidLaTeXOCR math formula image.
#
# Builds a CPU-based image running a lightweight FastAPI service for
# RapidLaTeXOCR from docker/math-ocr.Dockerfile. The Toolbox reaches it through
# RAPID_LATEX_OCR_URL and uses it as the `rapid-latex-ocr` provider for
# `math.recognize`.
#
# Usage:
#   ./scripts/build-math-ocr-image.sh            # latest
#   ./scripts/build-math-ocr-image.sh 0.1.1      # pinned
#
# Requirements: docker.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MATH_OCR_VERSION="${1:-latest}"
IMAGE_TAG="rapid-latex-ocr:${MATH_OCR_VERSION}"
DOCKERFILE="${PROJECT_DIR}/docker/math-ocr.Dockerfile"

echo "=== Build RapidLaTeXOCR image ==="
echo "Version: ${MATH_OCR_VERSION}"
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
echo "  docker compose up -d rapid-latex-ocr"
