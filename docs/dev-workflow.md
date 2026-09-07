# Development with Docker

This guide covers two ways to run the Acessilia Toolbox with Docker:

1. **Local build** — for agile development with source code
2. **Pre-published image** — to use without cloning the repository

## Prerequisites

- Docker Engine / Docker Desktop with Compose
- Git
- Python 3.11+ (for development outside the container)

---

## 1. Agile development with Docker Compose

The `docker-compose.yml` at the project root starts the Toolbox **with local build** together with all providers (docling-serve, MinIO, Valkey).

### Start the full environment

```bash
# 1. Configure environment variables
cp .env.example .env
# Edit .env with credentials (MINIO_ACCESS_KEY, MINIO_SECRET_KEY...)

# 2. Build and start all services
docker compose up --build -d

# 3. Verify everything is working
curl http://localhost:8002/v1/health
```

### Development with hot-reload

For interactive development, you can run the Toolbox outside the container (with uvicorn hot-reload) while providers run in Docker:

```bash
# Terminal 1: Providers via Docker
docker compose up -d docling-serve minio valkey

# Terminal 2: Toolbox with hot-reload
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export DOCLING_SERVE_URL=http://localhost:5001
export MINIO_URL=http://localhost:9000
export VALKEY_URL=redis://localhost:6379
uvicorn acessilia_toolbox.api.app:create_app --factory --host 0.0.0.0 --port 8002 --reload
```

> The `--reload` flag makes uvicorn restart the server automatically on every source code change.

### Stop the environment

```bash
docker compose down
# To also remove volumes (persistent data):
docker compose down -v
```

---

## 2. Using a pre-published GHCR image

If you don't need to modify the source code, you can use the already published image:

```bash
# Pull the latest develop image
docker pull ghcr.io/a11ydevs/acessilia-toolbox:develop

# Run
docker run --rm -p 8002:8002 \
  -e DOCLING_SERVE_URL=http://host.docker.internal:5001 \
  ghcr.io/a11ydevs/acessilia-toolbox:develop
```

Or use `docker-compose.staging.yml` which already references the GHCR image:

```bash
docker compose -f docker-compose.staging.yml up -d
```

---

## 3. Manual image build

```bash
# Basic build (API only)
docker build -t acessilia-toolbox:local .

# Build with extras (storage + cache)
docker build \
  --build-arg TOOLBOX_EXTRAS="api,storage,cache" \
  -t acessilia-toolbox:local .

# Run
docker run --rm -p 8002:8002 acessilia-toolbox:local
```

---

## 4. Tests

### Inside the container

```bash
# Build the test image
docker build --target test -t acessilia-toolbox:test .

# Run tests
docker run --rm acessilia-toolbox:test
```

### Locally (outside the container)

```bash
pytest tests/unit/ -v
```

---

## 5. Tips for agile development

### Using `.env.docker` inside Docker

The `.env.docker` file contains URLs using container names (e.g.: `http://docling-serve:5001`). When the Toolbox runs inside Docker Compose, copy or symlink it to `.env`:

```bash
cp .env.docker .env
docker compose up --build -d
```

### Quickly check logs

```bash
# Logs from all services
docker compose logs -f

# Logs from toolbox only
docker compose logs -f toolbox
```

### Run commands in a running container

```bash
docker compose exec toolbox python -c "import acessilia_toolbox; print(acessilia_toolbox.__version__)"
```