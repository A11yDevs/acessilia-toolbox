# Acessilia Toolbox
#
# Multi-stage build: the production image carries no ML runtime because
# providers run out of process (constitution, principle 4).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependencies for the filesystem artifact store (poppler-utils for
# the PDF builder). GL libraries are not required: the toolbox ships no ML
# runtimes, and PyMuPDF is not a mandatory dependency.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Core install (without the optional storage/cache extras).
RUN pip install --upgrade pip && pip install . && rm -rf ~/.cache

# === Production image: stateless, model-free, ~200 MB ===
FROM base AS production
COPY . .
EXPOSE 8000
ENV DOCLING_SERVE_URL=http://docling-serve:5001
CMD ["uvicorn", "acessilia_toolbox.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# === Test image ===
FROM base AS test
RUN pip install ".[dev]" && rm -rf ~/.cache
COPY . .
CMD ["pytest", "tests/", "-v"]