# API and Protocols

## Overview

Acessilia Toolbox is designed to expose the same normalized capabilities
through REST and MCP.

The exact wire schema is intentionally versioned and should evolve
independently from provider APIs.

## REST

Suggested resource groups:

``` text
GET  /v1/health
GET  /v1/capabilities
GET  /v1/capabilities/{capability_id}
POST /v1/capabilities/{capability_id}:execute

GET  /v1/providers
GET  /v1/providers/{provider_id}
GET  /v1/providers/{provider_id}/health

POST /v1/artifacts
GET  /v1/artifacts/{artifact_id}

GET  /v1/planning/domain
GET  /v1/planning/capabilities/{capability_id}
```

Long-running operations may require an external job/queue abstraction.
If asynchronous job state is exposed through the Toolbox, its
authoritative state must live in an external state service rather than
process memory.

## Execution request

``` json
{
  "input": {
    "artifact_id": "sha256:..."
  },
  "provider": "docling",
  "parameters": {}
}
```

## Execution response

``` json
{
  "status": "succeeded",
  "capability": "document.structure.extract",
  "provider": "docling",
  "artifacts": [
    {
      "artifact_id": "sha256:...",
      "media_type": "application/json"
    }
  ]
}
```

## MCP

MCP should expose capabilities as tools and descriptive/planning data as
resources.

Example tool names:

``` text
artifact_identify
document_ocr
document_structure_extract
document_convert
accessibility_validate
```

Example resources:

``` text
acessilia://capabilities
acessilia://providers
acessilia://planning/domain
```

MCP descriptions should remain concise and should not duplicate
provider-specific implementation details unless needed by the caller.

## OpenAPI

The REST API should publish OpenAPI. OpenAPI is authoritative for HTTP
syntax, schemas, status codes, and authentication. It is not a
replacement for PDDL planning semantics.

## Errors

Errors should be machine-readable and distinguish:

- invalid capability request;
- unsupported media type;
- unavailable provider;
- provider timeout;
- provider execution failure;
- artifact not found;
- authorization failure;
- capability contract violation.

Errors must not leak credentials or sensitive provider internals.

## Manual Testing

### Starting the server

```bash
# From the repository root, with docling-serve running:
export DOCLING_SERVE_URL=http://localhost:5001
.venv/bin/uvicorn "acessilia_toolbox.api.app:create_app" \
  --factory --host 0.0.0.0 --port 8000 --reload
```

The `--reload` flag watches for source changes and restarts automatically.

### Swagger UI

Once the server is running, open in a browser:

``` text
http://localhost:8000/v1/docs
```

Every endpoint can be exercised from the interactive Swagger UI.

### OpenAPI document

``` text
http://localhost:8000/v1/openapi.json
```

### smoke tests via curl

```bash
# Health check
curl http://localhost:8000/v1/health

# List capabilities
curl http://localhost:8000/v1/capabilities | python3 -m json.tool

# Capability detail (includes PDDL semantics)
curl http://localhost:8000/v1/capabilities/document.structure.extract | \
  python3 -m json.tool

# List registered providers
curl http://localhost:8000/v1/providers | python3 -m json.tool

# Provider health probe
curl http://localhost:8000/v1/providers/docling/health

# Extract a document
curl -X POST http://localhost:8000/v1/capabilities/document.structure.extract:execute \
  -F "file=@documento.pdf" \
  -F "language=pt-BR" | python3 -m json.tool | head -60

# Extract with a stored artifact (upload first, then reference by sha256)
curl -X POST http://localhost:8000/v1/artifacts \
  -F "file=@documento.pdf"
ARTIFACT_ID=$(curl -s -X POST http://localhost:8000/v1/artifacts \
  -F "file=@documento.pdf" | python3 -c "import sys,json;print(json.load(sys.stdin)['artifact_id'])")
curl -X POST "http://localhost:8000/v1/capabilities/document.structure.extract:execute" \
  -F "artifact_id=$ARTIFACT_ID" | python3 -m json.tool | head -30

# Retrieve a stored artifact
curl -o /dev/stdout http://localhost:8000/v1/artifacts/$ARTIFACT_ID > artifact.bin

# PDDL domain fragment
curl http://localhost:8000/v1/planning/domain

# PDDL predicates
curl http://localhost:8000/v1/planning/predicates

# PDDL action for a specific capability
curl http://localhost:8000/v1/planning/capabilities/document.structure.extract
```

### Testing without a real PDF

A minimal one-page PDF can be generated inline:

```python
python3 -c "
import struct
pdf = b'%PDF-1.4\\n1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\\n'
pdf += b'2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\\n'
pdf += b'3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]'
pdf += b'/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R>>>>>>endobj\\n'
pdf += b'4 0 obj<< /Length 44 >>stream\\nBT /F1 24 Tf 72 760 Td(Teste)Tj ET\\nendstream\\nendobj\\n'
pdf += b'5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\\n'
pdf += b'xref\\n0 6\\n0000000000 65535 f \\n'
for i, o in enumerate([1,2,3,4,5]):
    pdf += b'%010d 00000 n \\n' % (o,)
pdf += b'trailer<< /Size 6 /Root 1 0 R >>\\nstartxref\\n%d\\n%%%%EOF\\n' % len(pdf)
open('/tmp/teste.pdf', 'wb').write(pdf)
print('/tmp/teste.pdf criado')
"
```

Then extract it:

```bash
curl -X POST http://localhost:8000/v1/capabilities/document.structure.extract:execute \
  -F "file=@/tmp/teste.pdf" \
  -F "language=pt-BR" | python3 -c "
import sys,json
r = json.load(sys.stdin)
print('capability:', r['capability'])
print('provider :', r['provider'])
print('elements :', [(e['type'], e['text']) for e in r['document']['elements']])
print('cache_key:', r['provenance']['cache_key'][:30] + '...')
"
```

### Testing error handling

```bash
# Invalid media type
curl -X POST http://localhost:8000/v1/capabilities/document.structure.extract:execute \
  -F "file=@/tmp/teste.zip;type=application/zip"

# Unknown capability
curl http://localhost:8000/v1/capabilities/speech.synthesize

# Unknown provider
curl http://localhost:8000/v1/providers/mineru
```

### Stopping the server

Press `Ctrl+C` in the terminal running uvicorn, or close the terminal
session.
