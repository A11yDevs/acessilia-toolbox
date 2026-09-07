# Acessilia Toolbox
#
# Multi-stage build: the production image carries no ML runtime because
# providers run out of process (constitution, principle 4).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build metadata injected by CI (optional, for provenance labels).
ARG GIT_COMMIT
ARG IMAGE_TAG
ENV GIT_COMMIT=${GIT_COMMIT} IMAGE_TAG=${IMAGE_TAG}

# System dependencies for the filesystem artifact store (poppler-utils for
# the PDF builder). GL libraries are not required: the toolbox ships no ML
# runtimes, and PyMuPDF is not a mandatory dependency.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Core install without ML runtimes or provider client libraries.
# API extras bring only uvicorn and FastAPI; storage (boto3) and
# cache (redis) stay optional.
RUN pip install --upgrade pip && pip install "." && rm -rf ~/.cache

# === Production image: stateless, model-free, ~200 MB ===
FROM base AS production

# Which pip extras to install. Override at build time to enable optional
# provider client libraries such as storage (boto3) or cache (redis).
#   docker build --build-arg TOOLBOX_EXTRAS="api,storage,cache" ...
ARG TOOLBOX_EXTRAS=api

RUN if [ -n "$TOOLBOX_EXTRAS" ]; then \
        pip install --no-cache-dir ".[${TOOLBOX_EXTRAS}]"; \
    fi

COPY . .
EXPOSE 8002
ENV TOOLBOX_PORT=8002
CMD ["sh", "-c", "uvicorn acessilia_toolbox.api.app:create_app --factory --host 0.0.0.0 --port ${TOOLBOX_PORT:-8002}"]

# === Test image ===
FROM base AS test
RUN pip install ".[dev]" && rm -rf ~/.cache
COPY . .
CMD ["pytest", "tests/", "-v"]