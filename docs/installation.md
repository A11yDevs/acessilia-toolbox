# Installation

## Status

The new Toolbox architecture is a specification-first redesign. Exact
package names, container images, and commands should be updated as
implementation lands.

## Recommended development prerequisites

- Git
- Python 3.11+ if the initial implementation uses Python
- Docker Engine / Docker Desktop with Compose
- `curl` or an equivalent HTTP client
- optional provider containers such as docling-serve, MinerU, MinIO, or
  OCR services

## Clone

``` bash
git clone https://github.com/A11yDevs/acessilia-toolbox.git
cd acessilia-toolbox
```

## Local environment

A likely Python development workflow is:

``` bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

These commands are provisional until `pyproject.toml` is finalized.

## Configuration with environment variables

### 1. Create the `.env` file

The repository ships an `.env.example` with all configurable variables.
Copy it to `.env` (which is gitignored — secrets will never be
committed):

``` bash
cp .env.example .env
```

### 2. Edit the secrets

Open `.env` in your editor and replace the placeholder credentials with
real values.

#### MinIO (ACCESS_KEY / SECRET_KEY)

MinIO **requires** a credential pair to start — it is not possible to
disable this authentication. The Toolbox uses them to sign S3 requests
(AWS Signature V4) and protect artifact storage from unauthorized
access on the local network.

Choose strong values (use long, random passwords):

``` bash
# Use only alphanumeric characters — MinIO rejects special chars
# and dashes in ACCESS_KEY, and may reject non-alphanumeric SECRET_KEY.
MINIO_ACCESS_KEY=acessiliaadmin              # Alphanumeric only
MINIO_SECRET_KEY=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 32)
```

**How it works internally:** `providers-config.yaml` references these
variables via `${MINIO_ACCESS_KEY}` and `${MINIO_SECRET_KEY}`, and the
S3 adapter (`src/acessilia_toolbox/providers/storage.py`) passes the
values as `aws_access_key_id` and `aws_secret_access_key` to the boto3
connection. Request signing overhead is negligible (~0.5ms per call)
compared to payload transfer — **there is no performance gain in
removing it**.

> 💡 **Extra security:** The `docker-compose.yml` exposes the MinIO web
> console port (`9001`) to the host. On shared machines or if you want
> to reduce the attack surface, remove the `MINIO_CONSOLE_PORT` mapping
> and access the console only via `docker exec` or through Docker's
> internal network. For most local development scenarios, keeping the
> port exposed is convenient and safe. See `docker-compose.yml` for
> details.

#### Valkey

Valkey (cache) does not require authentication for local use. In
production, configure a password via `VALKEY_URL=redis://:password@host:6379`.

### 3. Understand every variable

| Variable | Default | Purpose |
|---|---|---|
| `DOCLING_SERVE_URL` | `http://localhost:5001` | docling-serve endpoint |
| `DOCLING_SERVE_IMAGE` | `ghcr.io/docling-project/docling-serve-cpu:v1.32.0` | Docker image for the extraction provider |
| `DOCLING_SERVE_PORT` | `5001` | Host-mapped port |
| `DOCLING_SERVE_ENABLE_UI` | `false` | Enable the docling web UI |
| `TOOLBOX_PROVIDERS_CONFIG` | `providers-config.yaml` | Path to the provider manifest |
| `MINIO_URL` | `http://localhost:9000` | MinIO endpoint (S3 storage) |
| `MINIO_PORT` | `9000` | S3 API port |
| `MINIO_CONSOLE_PORT` | `9001` | MinIO web console port |
| `MINIO_ACCESS_KEY` | `change-me` | **Replace** with a real key |
| `MINIO_SECRET_KEY` | `change-me-too` | **Replace** with a real secret |
| `VALKEY_URL` | `redis://localhost:6379` | Valkey cache endpoint |
| `VALKEY_PORT` | `6379` | Valkey port |
| `TOOLBOX_HOST` | `0.0.0.0` | uvicorn listen address |
| `TOOLBOX_PORT` | `8000` | Toolbox HTTP port |

### 4. Load the configuration

Both the REST server and the CLI read `.env` automatically when
started (via `python-dotenv`, configured in `pyproject.toml`).
If you prefer to load it manually:

``` bash
export $(grep -v '^#' .env | xargs)
```

## Provider isolation

Do not install Docling, MinerU, OCR ML stacks, or object-storage servers
as mandatory Toolbox dependencies. Run heavy providers separately.

### Starting providers with Docker Compose

The `docker-compose.yml` at the project root declares all three
external services the Toolbox depends on:

| Service | Port | Purpose |
|---|---|---|
| `docling-serve` | `5001` | Document structure extraction |
| `minio` | `9000` / `9001` | Artifact storage (S3) |
| `valkey` | `6379` | Operational cache |

Start everything with a single command:

``` bash
docker compose up -d
```

This reads the variables from your `.env` file automatically. Images
are pulled on first run and named volumes are created for persistent
data.

Check that all three services are healthy:

``` bash
docker compose ps
```

Expected output (all services should show `healthy` or `Up`):

``` text
NAME                 IMAGE                                          STATUS
acessilia-minio      minio/minio:RELEASE.2025-04-22T22-12-26Z       Up (healthy)
acessilia-valkey     valkey/valkey:8-alpine                          Up (healthy)
docling-serve        ghcr.io/docling-project/docling-serve-cpu:...   Up (healthy)
```

> **Note:** `docling-serve` may take 30–60 seconds to become healthy
> because it downloads ML models on the first request. Subsequent
> startups are faster since models are cached in the `docling-models`
> volume.

To stop the providers:

``` bash
docker compose down
```

To also remove persisted data volumes:

``` bash
docker compose down -v
```

### Starting a single provider manually (alternative)

If you prefer to run providers individually instead of using Compose:

``` bash
# docling-serve
docker run -d --name docling-serve -p 5001:5001 \
  -v docling-models:/root/.cache/docling \
  ghcr.io/docling-project/docling-serve-cpu:v1.32.0

# MinIO
docker run -d --name acessilia-minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=your-access-key \
  -e MINIO_ROOT_PASSWORD=your-secret-key \
  -v minio-data:/data \
  minio/minio:RELEASE.2025-04-22T22-12-26Z server /data --console-address ":9001"

# Valkey
docker run -d --name acessilia-valkey -p 6379:6379 \
  -v valkey-data:/data \
  valkey/valkey:8-alpine
```

### Provider topology file

The file `providers-config.yaml` declares how the Toolbox reaches each
provider. It uses the same `${VARIABLE}` placeholders from `.env`:

``` yaml
providers:
  - id: docling
    version: "1.32"
    transport: http
    endpoint: ${DOCLING_SERVE_URL}
    health_path: /health
    timeout_seconds: 600
    capabilities:
      - document.structure.extract
    media_types:
      - application/pdf
      - application/vnd.openxmlformats-officedocument.wordprocessingml.document
      - image/png
      - image/jpeg
      - image/tiff

  - id: minio
    version: "2025.04"
    transport: s3
    endpoint: ${MINIO_URL}
    capabilities:
      - artifact.store
      - artifact.retrieve
    config:
      bucket: acessilia
      access_key: ${MINIO_ACCESS_KEY}
      secret_key: ${MINIO_SECRET_KEY}

  - id: valkey
    version: "8"
    transport: redis
    endpoint: ${VALKEY_URL}
    capabilities:
      - cache.get
      - cache.put
    config:
      db: 0
      ttl_seconds: 604800
```

## Smoke tests (manual verification)

Once the environment is up, run these checks to confirm everything
is working.

### 1. Toolbox health check

``` bash
curl http://localhost:8000/v1/health | python3 -m json.tool
```

**Expected response:**
``` json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

### 2. List registered capabilities

``` bash
curl http://localhost:8000/v1/capabilities | python3 -m json.tool
```

Should show a list containing `document.structure.extract` with its
metadata (description, input/output schema, etc.).

### 3. List providers and check health

``` bash
curl http://localhost:8000/v1/providers | python3 -m json.tool
```

Should list docling, minio and valkey with their respective endpoints
and versions.

### 4. Extract structure from a PDF

If you don't have a PDF at hand, generate sample documents:

``` bash
python scripts/generate_samples.py
```

Then run the extraction:

``` bash
curl -X POST http://localhost:8000/v1/capabilities/document.structure.extract:execute \
  -F "file=@/tmp/sample-simple.pdf" \
  -F "language=pt-BR" | python3 -m json.tool | head -60
```

**Expected response:** a JSON with `status: "success"`, document
metadata (`page_count`, `element_count`), and the list of extracted
elements with `type` and `text`.

### 5. Verify cache behavior

After the first extraction, the result is cached in Valkey. Confirm
the cache entry was created:

``` bash
docker exec acessilia-valkey valkey-cli KEYS 'acessilia:execution:*'
```

**Expected output:** one or more keys starting with
`acessilia:execution:`. To inspect a specific entry:

``` bash
docker exec acessilia-valkey valkey-cli GET 'acessilia:execution:*' | head -c 200
```

> You can also watch cache metrics live:
> `docker exec -it acessilia-valkey valkey-cli INFO stats | grep -i hits`

Run the extraction again — it should complete much faster on a cache hit.
You can verify the cache hit via the `provenance.cache_hit` field in the
response.

### 6. Use the CLI (alternative to REST)

``` bash
# List capabilities
acessilia-toolbox capabilities

# List providers with health check
acessilia-toolbox providers --health

# Extract a document
acessilia-toolbox execute document.structure.extract \
  --document /tmp/sample-simple.pdf \
  --language pt-BR \
  --provenance
```

### 7. Check caching and storage (MinIO)

Access the MinIO web console at http://localhost:9001 and log in with
the credentials set in `.env`. The `acessilia` bucket should appear
after the first artifact is stored.

### 8. Provider logs

If something is not working, inspect the logs:

``` bash
docker compose logs docling-serve --tail 50
docker compose logs acessilia-minio --tail 20
docker compose logs acessilia-valkey --tail 20
```

### Quick troubleshooting

| Symptom | Likely cause | Solution |
|---|---|---|
| `connection refused` on docling | Provider not started or still downloading models | `docker compose logs docling-serve --tail 30` |
| `401` on MinIO | Credentials in `.env` don't match `providers-config.yaml` | Check `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` |
| `timeout` on extraction | File too large or docling overloaded | Increase `timeout_seconds` in `providers-config.yaml` |
| CLI command not found | Virtual environment not activated | `source .venv/bin/activate` |

## Production

A production deployment should allow Toolbox replicas to be replaced
without data loss. Persistent volumes belong to stateful providers, not
Toolbox containers.

``` text
Load Balancer
  +-> Toolbox replica 1
  +-> Toolbox replica 2
  +-> Toolbox replica N

External providers/storage/cache
```
