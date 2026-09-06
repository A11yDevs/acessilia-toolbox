# Capability Model

## Overview

The Acessilia Toolbox exposes **capabilities** — declarative operations that
agents or users can invoke. Each capability is implemented by an external
**provider**, running in its own process. The toolbox never embeds ML runtimes
or heavy provider libraries; it communicates with them over HTTP (or S3/Redis)
and normalizes the result into a canonical format.

```
┌─────────────────────────────────────────────────┐
│               Acessilia Toolbox                  │
│                                                   │
│  capabilities/*.yaml  ──►  CapabilityRegistry    │
│  providers-config.yaml ──►  ProviderRegistry      │
│                            ADAPTERS (Python)      │
│                                                   │
│  REST / MCP / CLI                                 │
└──────┬────────────────────────────────────────────┘
       │ HTTP / S3 / Redis
       ▼
┌─────────────┐  ┌────────┐  ┌────────┐
│ docling-serve│  │ MinIO  │  │ Valkey │  ← provedores externos
└─────────────┘  └────────┘  └────────┘
```

---

## 1. How the architecture works

### Three layers

| Layer | What it defines | Where it lives |
|---|---|---|
| **Capability** | The *what* (contract) | `capabilities/*.yaml` |
| **Provider** | The *who* (endpoint + transport) | `providers-config.yaml` |
| **Adapter** | The *how* (Python code) | `src/acessilia_toolbox/providers/` |

### Startup flow

1. `CapabilityRegistry.from_directory()` reads all `*.yaml` from `capabilities/`
2. `ProviderRegistry.from_file()` reads `providers-config.yaml` and resolves `${VAR}`
3. Each provider is matched to a Python adapter registered in `ADAPTERS`
4. `CapabilityExecutor` orchestrates: validate → resolve provider → invoke adapter → normalize → cache → return

### Naming

Use stable hierarchical identifiers:

``` text
document.structure.extract
artifact.store
artifact.retrieve
speech.synthesize
```

Provider names **must not** be embedded in capability IDs.

---

## 2. Capability contract (`capabilities/*.yaml`)

```yaml
# capabilities/document.structure.extract.yaml
id: document.structure.extract
version: 1
description: >-
  Extract the logical and visual structure of a document and normalize it into
  the canonical Acessilia structured document.

input:
  schema: artifact/document@1
  media_types:
    - application/pdf
    - image/png
    - image/jpeg
    - image/tiff

output:
  schema: artifact/structured-document@1
  media_types:
    - application/json

execution:
  deterministic: true    # same input → same output?
  idempotent: true       # safe to repeat without side effects?
  cacheable: true        # may be stored in cache?
  timeout_hint_seconds: 600

semantics:
  requires:
    - document_available
  produces:
    - structured
    - has_ast
    - text_available

providers:
  - id: docling           # must exist in providers-config.yaml
    version: "1.32+"
```

---

## 3. Provider contract (`providers-config.yaml`)

```yaml
providers:
  - id: docling
    version: "1.32"
    transport: http       # http | s3 | redis
    endpoint: ${DOCLING_SERVE_URL}
    health_path: /health
    timeout_seconds: 600
    capabilities:
      - document.structure.extract
    media_types:
      - application/pdf
```

---

## 4. How to add a new capability

Simply create a YAML file in `capabilities/`. **No Python code required.**

### Example: speech synthesis capability

```yaml
# capabilities/speech.synthesize.yaml
id: speech.synthesize
version: 1
description: >-
  Convert structured document text into accessible speech audio.

input:
  schema: artifact/text@1
  media_types:
    - application/json

output:
  schema: artifact/audio@1
  media_types:
    - audio/mpeg

execution:
  deterministic: false
  idempotent: false
  cacheable: true
  timeout_hint_seconds: 120

semantics:
  requires:
    - structured
    - text_available
  produces:
    - audio_available

providers:
  - id: tts-service
```

The toolbox now exposes the capability via REST (`GET /v1/capabilities/speech.synthesize`)
and MCP. Trying to execute it without a provider returns a clear error:
"no provider implements speech.synthesize".

---

## 5. How to add a new provider

### 5.1 Register in `providers-config.yaml`

```yaml
  - id: tts-service
    version: "1.0"
    transport: http
    endpoint: ${TTS_SERVICE_URL}
    health_path: /health
    timeout_seconds: 120
    capabilities:
      - speech.synthesize
    media_types:
      - application/json
```

### 5.2 Write the adapter

The adapter implements the `ProviderAdapter` protocol:

```python
# src/acessilia_toolbox/providers/tts.py
"""TTS service adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth


class TTSAdapter:
    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> Any:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        try:
            with httpx.Client(
                base_url=self.base_url, timeout=self.descriptor.timeout_seconds
            ) as client:
                response = client.post(
                    "/v1/synthesize",
                    content=payload,
                    headers={"Content-Type": media_type},
                )
                response.raise_for_status()
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"TTS service unreachable: {exc}", provider=self.descriptor.id
            ) from exc

        return response.content

    def versions(self) -> dict[str, str]:
        with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
            resp = client.get("/version")
            resp.raise_for_status()
            return {"provider": resp.json().get("version", "unknown")}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
                resp = client.get(self.descriptor.health_path)
                resp.raise_for_status()
                return ProviderHealth(
                    provider=self.descriptor.id,
                    healthy=True,
                    version=resp.json().get("version"),
                    checked_at=checked_at,
                )
        except Exception as exc:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                checked_at=checked_at,
            )
```

### 5.3 Register the adapter in the `ADAPTERS` dict

```python
# providers/__init__.py
from acessilia_toolbox.providers.tts import TTSAdapter

ADAPTERS = {
    "docling": DoclingProvider,
    "tts-service": TTSAdapter,     # ← new adapter
}
```

---

## 6. How to add provider dependencies

### If the adapter only uses `httpx` (already in core)

Nothing to do. `httpx` is a mandatory dependency.

### If a specific client library is needed

1. Declare the optional extra in `pyproject.toml`:

```toml
[project.optional-dependencies]
tts = ["elevenlabs>=1.0"]
```

2. Use **lazy import** in the adapter (explained in section 7).

3. To use in a Docker image, create a derived image:

```dockerfile
# Dockerfile.tts
FROM acessilia-toolbox:latest
RUN pip install --no-cache-dir "acessilia-toolbox[tts]"
```

```bash
docker build -t acessilia-toolbox-tts -f Dockerfile.tts .
```

---

## 7. Lazy import and `ConfigurationError` handling

### What is lazy import

Instead of importing at the top of the module (which would fail if the library
is not installed), the import is done **inside the method**:

```python
# ❌ Top-level import — crashes the entire startup
import boto3

# ✅ Lazy import — fails only when the code path is actually used
def __init__(self, descriptor):
    try:
        import boto3
    except ImportError as exc:
        raise ConfigurationError(
            "S3 storage requires the 'storage' extra: "
            "pip install 'acessilia-toolbox[storage]'",
            provider=descriptor.id,
        ) from exc
```

### How the toolbox handles it at startup

In `app.py`, `_store_from()` and `_cache_from()` wrap the calls in
`try/except`:

```python
def _store_from(providers):
    descriptor = candidates[0]
    try:
        store = _create_store(descriptor)
    except ConfigurationError as exc:          # ← catches missing extra
        LOG.warning("artifact store disabled: %s", exc)
        return None                            # ← proceeds without store
    return store
```

**Behavior:**
- If `boto3` is not installed → logs `WARNING` → `app.state.store = None`
- If `redis` is not installed → logs `WARNING` → `app.state.cache = None`
- The toolbox starts **without store/cache**, other capabilities work fine
- Error only happens **at execution time** when the absent resource is actually used

### Why lazy import is better than a plugin system

| Approach | Complexity | Overhead |
|---|---|---|
| Lazy import + extras | Minimal | Zero |
| Plugin folder with dynamic discovery | Medium | Loading framework |
| Plugin registry with entry points | High | importlib.metadata + versioning |

For the toolbox, lazy import is the right choice because:
- Dependencies are **HTTP/protocol clients**, not heavy runtimes
- The number of extras is small and known at build time
- The separation is already declarative via `pyproject.toml`

---

## 8. When a Docker image rebuild is needed

| What changes | Rebuild required? |
|---|---|
| New `capabilities/*.yaml` | **No** — mount as volume |
| `providers-config.yaml` (new endpoint) | **No** — mount as volume |
| New Python adapter | **Yes** — new code |
| `pyproject.toml` — new optional extra | **Yes** — new pip dependency |
| Environment variable only | **No** — `--env-file` or `-e` |
| Schema `schemas/*.json` | **No** — mount as volume |

### Example: mount everything mutable without rebuild

```bash
docker run -d --name acessilia-toolbox \
  -p 8002:8002 \
  --network acessilia-toolbox_default \
  -v ./capabilities:/app/capabilities \
  -v ./providers-config.yaml:/app/providers-config.yaml \
  -v ./schemas:/app/schemas \
  -v ./src:/app/src \
  --env-file .env \
  acessilia-toolbox:latest
```

This allows adding capabilities, providers and even Python code without
rebuilding the image — the source code is mounted directly in the container.

---

## 9. How to manage capabilities and providers

### Via REST API

```bash
# List capabilities
curl http://localhost:8002/v1/capabilities

# Capability detail
curl http://localhost:8002/v1/capabilities/document.structure.extract

# List providers
curl http://localhost:8002/v1/providers

# Provider detail
curl http://localhost:8002/v1/providers/docling

# Provider health check
curl http://localhost:8002/v1/providers/docling/health

# PDDL domain (planning)
curl http://localhost:8002/v1/planning/domain

# Execute a capability
curl -X POST http://localhost:8002/v1/capabilities/document.structure.extract:execute \
  -F "file=@documento.pdf" \
  -F "language=pt-BR"
```

### Via CLI

```bash
# List capabilities
acessilia-toolbox capabilities

# List providers with health check
acessilia-toolbox providers --health

# JSON output for processing
acessilia-toolbox capabilities --json | python3 -m json.tool
```

### Via MCP

The MCP server (`scripts/mcp_server.py`) exposes each capability as a
tool that AI agents can invoke through the MCP protocol.

### Update configuration without rebuild

Since capabilities and providers are defined in YAML, any text editor
suffices. Mount the files as volumes in the container (section 8) and restart:

```bash
docker restart acessilia-toolbox
```

If you want a runtime reload endpoint without restart, add to `app.py`:

```python
@app.post("/v1/reload")
def reload(request: Request):
    config_path = Path(os.getenv("TOOLBOX_PROVIDERS_CONFIG", "providers-config.yaml"))
    caps_dir = Path(os.getenv("TOOLBOX_CAPABILITIES_DIR", "capabilities"))
    request.app.state.capabilities = CapabilityRegistry.from_directory(caps_dir)
    request.app.state.providers = ProviderRegistry.from_file(config_path)
    request.app.state.executor = CapabilityExecutor(...)
    return {"status": "reloaded"}
```

---

## 10. Complete example: adding TTS from scratch

```
1. Create capabilities/speech.synthesize.yaml       (YAML only)
2. Add tts-service provider in providers-config.yaml  (YAML only)
3. Create src/acessilia_toolbox/providers/tts.py      (Python adapter)
4. Register in providers/__init__.py                   (1 line)
```

```bash
# If using mounted volumes:
docker restart acessilia-toolbox

# Verify:
curl http://localhost:8002/v1/capabilities/speech.synthesize

# Test provider health:
curl http://localhost:8002/v1/providers/tts-service/health
```

---

## 11. Testing capabilities and providers

The test suite follows a **three-layer** pattern, mirroring the architecture:

| Layer | What it tests | Real provider? |
|---|---|---|
| **Unit** | Registry, executor, cache key, schemas | No (stubs) |
| **Integration** | REST endpoints, artifact API | No (stubs) |
| **Contract** | Provider conformance to contract | Yes |

### Contract tests for store (MinIO/S3)

```bash
# Requires MinIO running (docker compose up -d)
export $(grep -v '^#' .env | xargs)
poetry run pytest tests/contract/test_artifact_store.py -v
```

Covers: round-trip, content-addressable identity, metadata, idempotency,
missing artifact error, `ArtifactRef` portability.

### Contract tests for cache (Valkey/Redis)

```bash
# Requires Valkey running (docker compose up -d)
export $(grep -v '^#' .env | xargs)
poetry run pytest tests/contract/test_cache.py -v
```

Covers: round-trip, key prefix, TTL, cache miss, graceful degradation,
cache key derivation, prefix isolation.

### Behavior when the provider is offline

Tests use `pytest.skip()` to silently skip — the fast suite never fails for
missing containers:

```python
@pytest.fixture(scope="session")
def live_store(store_descriptor):
    store = create_artifact_store(store_descriptor)
    if not isinstance(store, S3ArtifactStore):
        pytest.skip("factory did not return an S3 store")
    try:
        store._client.list_buckets()
    except Exception as exc:
        pytest.skip(f"MinIO unreachable: {exc}")
    return store
```

### Expected results

```
# Container running
17 passed in 0.69s

# Container offline
17 skipped in 0.01s
```

### Appendix: mandatory vs. optional dependencies

```
pip install .                        # core: pydantic, httpx, jsonschema, PyYAML
pip install .[api]                   # + fastapi, uvicorn, python-multipart
pip install .[storage]               # + boto3 (MinIO/S3)
pip install .[cache]                 # + redis (Valkey)
pip install .[dev]                   # + pytest, ruff, mypy, all extras above
```

The official image (`acessilia-toolbox:latest`) installs only `.[api]` — ~200 MB.
To add storage or cache, create a derived image (section 6).
