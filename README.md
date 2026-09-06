# Acessilia Toolbox

**A stateless, composable toolbox for exposing deterministic
capabilities to agentic systems via REST and MCP.**

---

## Quick start

```bash
# 1. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Start the extraction provider (Docker required)
docker run -d --name docling-serve -p 5001:5001 \
  -v docling-models:/root/.cache/docling \
  ghcr.io/docling-project/docling-serve-cpu:v1.32.0

# 3. Configure
export DOCLING_SERVE_URL=http://localhost:5001

# 4. Start the toolbox
uvicorn "acessilia_toolbox.api.app:create_app" --factory --host 0.0.0.0 --port 8002

# 5. Open Swagger UI
open http://localhost:8002/v1/docs
```

### Test in one command

```bash
# Health check
curl http://localhost:8002/v1/health

# List capabilities
curl http://localhost:8002/v1/capabilities | python3 -m json.tool

# Extract a document (replace with any PDF)
curl -X POST http://localhost:8002/v1/capabilities/document.structure.extract:execute \
  -F "file=@/path/to/document.pdf" \
  -F "language=pt-BR" | python3 -m json.tool | head -50
```

### No PDF at hand? Generate sample documents

```bash
python scripts/generate_samples.py
```

This creates four PDFs in `/tmp/`:

| File | Content | Purpose |
|---|---|---|
| `sample-simple.pdf` | One heading + one paragraph | Quick smoke test |
| `sample-report.pdf` | ~380 words, sections, numbers | Rich structure, metadata |
| `sample-mixed.pdf` | Lists, code block, headings | Element type diversity |
| `sample-multi-page.pdf` | 3 pages with unique content | Page-level extraction |

```bash
# Extract the report
curl -X POST http://localhost:8000/v1/capabilities/document.structure.extract:execute \
  -F "file=@/tmp/sample-report.pdf" \
  -F "language=en-US" | python3 -c "
import sys, json
r = json.load(sys.stdin)
for e in r['document']['elements']:
    print(f\"  [{e['type']}] {e['text']}\")
print(f\"  -> {r['provenance']['duration_ms']}ms via {r['provenance']['provider_version']}\")
"
```

Example output:

```
  [heading] Annual Report 2025
  [paragraph] This report summarizes the financial performance...
  [heading] 1. Executive Summary
  [paragraph] Revenue grew 15% year-over-year...
  [heading] 2. Financial Highlights
  [paragraph] Total assets: $18.7M  |  Net income: $3.2M  |  EPS: $1.45
  -> 2110ms via 1.32.0
```

---

## What is the toolbox?

A capability gateway. It exposes **what can be done** (extract structure,
OCR, store artifacts, validate accessibility) through a provider-neutral
interface, so agents and applications never couple to Docling, MinerU or
any other vendor.

| Concept | Meaning |
|---|---|
| **Capability** | Stable, provider-neutral operation (`document.structure.extract`) |
| **Provider** | External service that implements a capability (`docling-serve`) |
| **Toolbox** | Mediates between agents and providers — validates, routes, caches, normalizes |
| **Agentic Core** | Owns goals, planning, provider selection — **outside** the toolbox |

## Architecture

```mermaid
flowchart TB
    U[User] --> A[Agentic Core]
    A --> P[PDDL Planner]
    P --> A
    A -->|REST / MCP| T[Acessilia Toolbox]
    T --> D[docling-serve]
    T --> M[MinerU]
    T --> O[OCR]
    T --> S[MinIO]
    T --> C[Valkey Cache]
```

## What it can do today

| Capability | Provider | Status |
|---|---|---|
| `document.structure.extract` | docling-serve | ✅ |
| `artifact.store` / `artifact.retrieve` | filesystem, MinIO (optional) | ✅ |
| `cache.get` / `cache.put` | Valkey (optional) | ✅ |

## Key endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/health` | Service status |
| `GET` | `/v1/capabilities` | List capabilities |
| `POST` | `/v1/capabilities/{id}:execute` | Execute a capability |
| `GET` | `/v1/providers` | List providers |
| `POST` | `/v1/artifacts` | Store content |
| `GET` | `/v1/artifacts/{id}` | Retrieve content |
| `GET` | `/v1/planning/domain` | PDDL domain fragment |
| `GET` | `/v1/planning/predicates` | PDDL predicates |

> Full API reference: [docs/api.md](docs/api.md)

## CLI

```bash
# List capabilities
acessilia-toolbox capabilities

# List providers and probe their health
acessilia-toolbox providers --health

# Execute a capability
acessilia-toolbox execute document.structure.extract documento.pdf -o resultado.json

# Execute with provenance on stderr
acessilia-toolbox execute document.structure.extract documento.pdf --provenance
```

### Test with sample documents

```bash
# Generate the sample PDFs
python scripts/generate_samples.py

# Extract the report
acessilia-toolbox execute document.structure.extract /tmp/sample-report.pdf \
  -o /tmp/report.json --provenance 2>/tmp/provenance.json

# Inspect the output
python3 -c "
import json
d = json.load(open('/tmp/report.json'))
for e in d['elements']:
    print(f\"  [{e['type']}] {e['text'][:80]}\")
"

# Extract the multi-page document
acessilia-toolbox execute document.structure.extract /tmp/sample-multi-page.pdf \
  -o /tmp/multi.json
python3 -c "
import json
d = json.load(open('/tmp/multi.json'))
print(f\"Pages: {d['summary']['page_count']}, Elements: {d['summary']['element_count']}\")
"

# List capabilities as JSON
acessilia-toolbox capabilities --json | python3 -m json.tool | head -20

# List providers as JSON (credentials redacted)
acessilia-toolbox providers --json | python3 -m json.tool
```

Example output:

```
$ acessilia-toolbox execute document.structure.extract /tmp/sample-report.pdf \
    --provenance 2>/dev/null
document.structure.extract@1 via docling -> /tmp/sample-report.structured-document.json
pages: 1; elements: 7; obligations: 0
```

## Documentation

| Document | What it covers |
|---|---|
| [Architecture](docs/architecture.md) | Boundaries, components, state ownership |
| [Installation](docs/installation.md) | Setup, Docker, config |
| [API](docs/api.md) | REST, MCP, error codes, manual testing |
| [Capability Model](docs/capability-model.md) | Contracts, manifests, interchangeability |
| [PDDL Integration](docs/pddl.md) | Planning semantics |
| [Testing](docs/testing.md) | Test layers, snapshot validation |
| [Constitution](docs/constitution.md) | Design principles |
| [Contributing](docs/contribution.md) | Workflow, PR checklist |

## License

MIT. Provider services retain their own licenses.
