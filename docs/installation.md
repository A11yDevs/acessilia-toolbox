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

When a later version adds keys to `.env.example`, bring them into your `.env`
without touching the values already there:

``` bash
python scripts/sync_env.py          # appends the missing keys with their comments
python scripts/sync_env.py --check  # changes nothing; exit 1 if keys are missing
```

The script also lists keys of `.env` that the example no longer declares
(probably deprecated when no versioned file mentions them), keys set twice,
and values that `scripts/staging-update.sh` cannot `source` unquoted.

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
| `MINIO_IMAGE` | Pinned Chainguard MinIO digest in Compose | Override with an image containing `minio`, `mc`, and `sh` |
| `MINIO_PORT` | `9000` | S3 API port |
| `MINIO_CONSOLE_PORT` | `9001` | MinIO web console port |
| `MINIO_ACCESS_KEY` | `change-me` | **Replace** with a real key |
| `MINIO_SECRET_KEY` | `change-me-too` | **Replace** with a real secret |
| `ARTIFACT_FALLBACK_SOURCE` | `artifact-fallback` | Named volume or host path for filesystem failover; use a shared mount for replicas on different hosts |
| `TOOLBOX_ARTIFACT_FALLBACK_MODE` | `disabled` | `disabled` propagates S3 write failures (inline-only results are still returned, without a persisted location); `local` permits single-instance fallback; `shared` declares a filesystem accessible by all replicas |
| `VALKEY_URL` | `redis://localhost:6379` | Valkey cache endpoint |
| `VALKEY_PORT` | `6379` | Valkey port |
| `TOOLBOX_HOST` | `0.0.0.0` | uvicorn listen address |
| `TOOLBOX_PORT` | `8002` | Toolbox HTTP port |
| `TOOLBOX_API_KEY` | *(empty)* | Bearer token for REST API auth. Required: without it the application refuses to start. |
| `TOOLBOX_ALLOW_UNAUTHENTICATED` | *(empty)* | `1` starts without `TOOLBOX_API_KEY`, every endpoint open. Local development only. |

#### Artifact fallback and multiple replicas

With the default `TOOLBOX_ARTIFACT_FALLBACK_MODE=disabled`, a failed S3 write
returns HTTP 503 for uploads, by-reference results and binary outputs.
Inline-only results are returned without a persisted location (see below). It
never acknowledges an upload stored only on one replica's private disk. Existing backup objects remain readable when an S3
store is configured. The mode controls S3 failover writes only. Without a
configured S3 store, the filesystem remains the primary store in every mode,
including `disabled`. Filesystem-only deployments on multiple hosts still need
shared storage if requests can reach different replicas.

For a single instance, set `TOOLBOX_ARTIFACT_FALLBACK_MODE=local` to retain
the former behavior. Files saved during an outage are tied to that disk and
may become unavailable when the instance or its host is replaced.

For replicas on different hosts, mount the same distributed filesystem on
every host, then configure both Compose deployments with, for example:

```dotenv
TOOLBOX_ARTIFACT_FALLBACK_MODE=shared
ARTIFACT_FALLBACK_SOURCE=/mnt/toolbox-shared
```

`/mnt/toolbox-shared` must already be an actual shared mount, writable by the
container user on every host, supporting atomic file replacement. Setting
`shared` is an operator declaration: the Toolbox cannot verify the topology or
provision a distributed filesystem. A Docker named volume with the same name
on two hosts is still two independent volumes. Check that the mount is present
before starting containers; a missing mount must not silently become a local
directory. Monitor its availability and capacity.

Before rollout, stop S3, upload through replica A and download the returned ID
through replica B, verifying its SHA-256. Stop A and repeat the download from B.
After S3 recovery, confirm that backup objects remain readable. No automatic
replication back to S3 is performed. If the shared filesystem is unavailable,
the write must fail; private local disks must not be substituted for it.

Existing deployments that depended on automatic local fallback must explicitly
choose `local` for one instance or provision and verify shared storage before
choosing `shared`. Python callers constructing `FailoverArtifactStore` directly
must pass the same choice as `fallback_mode="local"` or `fallback_mode="shared"`.
The constructor defaults to disabled fallback writes, like the REST factory.

#### Inline extraction and cached artifacts during storage outages

An operation returning only inline JSON can complete during a temporary
storage outage. Its response identifies the JSON content without a persisted
URI/backend. The result is still written to the execution cache, so repeated
requests during the outage are served from the cache instead of extracting
again. After storage recovers, the never-written object invalidates that
entry: the next request extracts once more, publishes the JSON and replaces
the cache entry. Binary and by-reference outputs still require storage.

For a warm execution cache, the executor checks stored references before
returning them. This costs one `exists` call per artifact, including an S3
round trip for the JSON. If storage is temporarily unavailable and the
contract permits inline-only JSON, the cached document is returned without
reexecuting the extraction provider or claiming a verified stored download.
The cache entry is left unchanged so its stored reference can be verified
again after recovery. An object verified as missing invalidates the cache;
required binary/by-reference objects cannot use the inline exception.

The Core fallback after an unsuccessful initial upload depends on this inline
response behavior. Required output persistence failures remain errors; this
does not guarantee that a complete conversion with mandatory binary exports
can finish while storage is unavailable.

#### Orientation cache identity and version probes

With `auto_rotate` enabled for a supported image, orientation still runs before
the extraction cache lookup. The key includes the source and oriented image
fingerprints, provider/configuration identity and the effective orientation
decision. Confidence is retained in provenance but excluded from cache identity.

RapidOCR version metadata is memoized per process for 30 seconds, across adapter
instances, with a bounded cache. Deployment, declared version and configuration
changes select a different entry. Failed refreshes retain the last successful
identity and retry after 5 seconds; before any successful probe, the declared
version is used. Thus model-version changes can take up to the TTL to appear in
cache identity, and an unavailable version endpoint cannot immediately invalidate
otherwise usable cached extraction. Effective oriented bytes and decisions are
still evaluated on every auto-rotate request. Restarting the process clears the
version cache. No cross-process synchronization is provided.

For release migration details, see [0.2.0 storage changes](release-notes-0.2.0.md).

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

Both Compose files use the public Chainguard MinIO image, pinned by digest
for reproducible deployments. The image includes the server, `mc` for the
healthcheck, and a shell for bucket initialization. `minio-init` exits with
an error if bucket initialization fails. Set `MINIO_IMAGE` only when using
an image with these same tools.
The server runs as the image's non-root user (UID/GID `65532:65532`).
Before starting it with an existing root-owned volume, stop MinIO and
migrate the volume ownership once:

``` bash
docker compose stop minio
docker compose run --rm --no-deps --user 0:0 --entrypoint sh minio \
  -ec 'chown -R 65532:65532 /data'
docker compose up -d minio minio-init
```

For staging, add `-f docker-compose.staging.yml` to each Compose command.
The temporary migration container runs as root; the server does not.

Check that all three services are healthy:

``` bash
docker compose ps
```

Expected output (all services should show `healthy` or `Up`):

``` text
NAME                 IMAGE                                          STATUS
acessilia-minio      cgr.dev/chainguard/minio@sha256:...              Up (healthy)
acessilia-valkey     valkey/valkey:8-alpine                          Up (healthy)
docling-serve        ghcr.io/docling-project/docling-serve-cpu:...   Up (healthy)
```

> **Note:** `docling-serve` may take 30–60 seconds to become healthy
> because it downloads ML models on the first request. Subsequent
> startups are faster since models are cached in the `docling-models`
> volume, which is mounted at the image's own cache path
> (`/opt/app-root/src/.cache/docling`).

### Enabling the VLM pipeline (`chem.recognize`)

`chem.recognize` runs docling-serve's VLM pipeline, which needs the Granite
Vision model. The CPU image does not ship it, so the first call fails with
`Model 'ibm-granite/granite-docling-258M' not found in artifacts_path` until it
is downloaded into the cache volume:

``` bash
docker compose exec docling-serve \
  docling-tools models download-hf-repo ibm-granite/granite-docling-258M
```

The download lands in the mounted volume, so it survives container recreation.
Without it, `chem.recognize` answers `502` while every other docling-backed
capability keeps working.

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
# The image caches models under /opt/app-root/src/.cache/docling/models, so the
# volume must be mounted there; mounting /root/.cache/docling leaves it empty.
docker run -d --name docling-serve -p 5001:5001 \
  -e DOCLING_SERVE_ARTIFACTS_PATH=/opt/app-root/src/.cache/docling/models \
  -v docling-models:/opt/app-root/src/.cache/docling \
  ghcr.io/docling-project/docling-serve-cpu:v1.32.0

# MinIO
docker run -d --name acessilia-minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=your-access-key \
  -e MINIO_ROOT_PASSWORD=your-secret-key \
  -v minio-data:/data \
  cgr.dev/chainguard/minio@sha256:4cf4831a2bbcf13ddca09c1cbcc9faff716dd3c4247e0babc32864b8ee8e0034 server /data --console-address ":9001"

# Valkey
docker run -d --name acessilia-valkey -p 6379:6379 \
  -v valkey-data:/data \
  valkey/valkey:8-alpine

# audiveris-serve (optical music recognition, optional)
# Built from the repository recipe; the Toolbox image never carries the OMR
# engine itself.
docker build -f docker/audiveris-serve.Dockerfile -t audiveris-serve:latest .
docker run -d --name audiveris-serve -p 5003:5003 audiveris-serve:latest
```

### Sidecar images

The `Sidecars` workflow builds every sidecar from its `docker/` recipe and
publishes it to GHCR as `ghcr.io/a11ydevs/acessilia-toolbox-<sidecar>`, tagged
like the Toolbox image: the branch (`develop`, `main`, `release-x.y.z`),
`sha-<7>`, `latest` on `main` and the version on a release tag.
`docker-compose.staging.yml` pulls these images for the tracked branch, and
`scripts/staging-update.sh` updates them together with the Toolbox, so no
server builds a sidecar. Each image backs capabilities that stay unavailable
while its sidecar is not running:

| Sidecar (Compose service) | Image suffix | Recipe | Capability |
|---|---|---|---|
| `mineru-serve` | `mineru` | `docker/mineru-cpu.Dockerfile` | `document.structure.extract`, `document.layout.analyze`, `document.ocr` |
| `nougat-serve` | `nougat` | `docker/nougat-cpu.Dockerfile` | `document.structure.extract` |
| `rapid-latex-ocr` | `rapid-latex-ocr` | `docker/math-ocr.Dockerfile` | `math.recognize` |
| `pp-formulanet` | `pp-formulanet` | `docker/pp-formulanet.Dockerfile` | `math.recognize` |
| `audiveris-serve` | `audiveris-serve` | `docker/audiveris-serve.Dockerfile` | `music.omr` |
| `rapidocr-orient` | `rapidocr-orient` | `docker/rapidocr-orient.Dockerfile` | `image.page.orient` |
| `pdf-ua-render` | `pdf-ua-render` | `docker/pdf-ua-render.Dockerfile` | `document.export.pdf_ua`, `document.export.docx` |
| `verapdf-validate` | `verapdf-validate` | `docker/verapdf-validate.Dockerfile` | `pdf.validate.pdf_ua` |
| `math-speech` | `math-speech` | `docker/math-speech.Dockerfile` | `math.convert`, `math.verbalize` (provider pending, see #64) |

The `pdf-ua-render` build renders a smoke-test PDF and DOCX, the
`verapdf-validate` build validates a smoke-test PDF, and the `math-speech`
build converts, speaks and brailles test formulas, so a broken toolchain
fails CI rather than the first request.

To run a local build instead (a recipe change under test, or the GPU variant
of MinerU), build it and point the matching `*_IMAGE` variable at it, for
example:

```bash
docker build -f docker/pdf-ua-render.Dockerfile -t pdf-ua-render:local .
echo "PDF_UA_RENDER_IMAGE=pdf-ua-render:local" >> .env
```

`scripts/staging-update.sh` leaves a sidecar with such an override alone.
`scripts/build-mineru-image.sh`, `build-nougat-image.sh`,
`build-math-ocr-image.sh` and `build-pp-formulanet-image.sh` still build those
images locally.

The Toolbox starts regardless: a provider whose endpoint does not resolve is
reported as unhealthy and its capability answers `503`, while every other
capability keeps working.

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
curl http://localhost:8002/v1/health | python3 -m json.tool
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
curl http://localhost:8002/v1/capabilities | python3 -m json.tool
```

Should show a list containing `document.structure.extract` with its
metadata (description, input/output schema, etc.).

### 3. List providers and check health

``` bash
curl http://localhost:8002/v1/providers | python3 -m json.tool
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
curl -X POST http://localhost:8002/v1/capabilities/document.structure.extract:execute \
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

### Execution concurrency

`TOOLBOX_MAX_CONCURRENT_EXECUTIONS` limits concurrent capability executions per API process (default: `4`, minimum: `1`). Excess executions receive HTTP 503; clients can retry after capacity becomes available. Size this limit for the memory and capacity of the connected providers.
