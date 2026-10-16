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

# System dependencies:
#   poppler-utils — PDF builder for the filesystem artifact store.
#   curl          — used by the image HEALTHCHECK below and by the compose
#                   healthchecks, which probe /v1/health with curl.
# GL libraries are not required: the toolbox ships no ML runtimes, and PyMuPDF
# is not a mandatory dependency.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

# uv only exports the locked dependency set; pip installs it with hashes
# verified, so the image gets exactly what uv.lock pins (including the
# acessilia-docstruct release wheel) instead of a fresh resolution.
COPY --from=ghcr.io/astral-sh/uv:0.11.17@sha256:03bdc89bb9798628846e60c3a9ad19006c8c3c724ccd2985a33145c039a0577b /uv /usr/local/bin/uv

COPY pyproject.toml README.md uv.lock ./
COPY src/ ./src/

# Core install without ML runtimes or provider client libraries.
# API extras bring only uvicorn and FastAPI; storage (boto3) and
# cache (redis) stay optional. --locked fails the build if uv.lock is stale.
RUN pip install --upgrade pip \
    && uv export --locked --no-dev --no-emit-project --format requirements-txt \
        -o /tmp/requirements.txt \
    && pip install --require-hashes -r /tmp/requirements.txt \
    && pip install --no-deps "." \
    && rm -rf ~/.cache /tmp/requirements.txt

# === Production image: stateless, model-free, ~200 MB ===
FROM base AS production

# Which pip extras to install. Override at build time to enable optional
# provider client libraries such as storage (boto3) or cache (redis).
#   docker build --build-arg TOOLBOX_EXTRAS="api,storage,cache" ...
ARG TOOLBOX_EXTRAS=api

RUN if [ -n "$TOOLBOX_EXTRAS" ]; then \
        extras=$(echo "$TOOLBOX_EXTRAS" | tr ',' '\n' | sed 's/^/--extra /' | tr '\n' ' '); \
        uv export --locked --no-dev --no-emit-project --format requirements-txt \
            $extras -o /tmp/requirements.txt \
        && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
        && rm -f /tmp/requirements.txt; \
    fi

COPY . .
EXPOSE 8002
ENV TOOLBOX_PORT=8002
# Mirrors the compose healthcheck so the image is self-describing when run
# without compose.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://localhost:${TOOLBOX_PORT:-8002}/v1/health || exit 1
CMD ["sh", "-c", "uvicorn acessilia_toolbox.api.app:create_app --factory --host 0.0.0.0 --port ${TOOLBOX_PORT:-8002}"]

# === Test image ===
FROM base AS test
RUN uv export --locked --no-dev --no-emit-project --format requirements-txt \
        --extra dev -o /tmp/requirements.txt \
    && pip install --require-hashes -r /tmp/requirements.txt \
    && rm -rf ~/.cache /tmp/requirements.txt
COPY . .
CMD ["pytest", "tests/", "-v"]