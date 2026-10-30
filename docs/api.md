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

### Capability catalogue: `providers` and `declared_providers`

`GET /v1/capabilities` and `GET /v1/capabilities/{capability_id}` return two
provider lists per capability:

| Field | Content |
|---|---|
| `providers` | Providers **registered in this deployment** that implement the capability: what a client can execute here. |
| `declared_providers` | Providers the **manifest binds**, including opt-in ones this deployment does not register (e.g. `teleocr`). |

``` json
{
  "id": "document.structure.extract",
  "providers": ["docling", "mineru", "nougat"],
  "declared_providers": ["docling", "mineru", "nougat", "teleocr"]
}
```

The two lists are independent: `providers` is **not** a subset of
`declared_providers`. The executor accepts any registered provider that
implements the capability, bound in the manifest or not, so such a provider is
listed in `providers` only.

> **Contract change.** `providers` used to list the manifest bindings (what
> `declared_providers` lists now). The field keeps its name and type, but its
> content changed: a client that enumerated the bindings now sees the
> executable providers, which can be fewer (an opt-in provider not registered)
> or more (a registered provider the manifest does not bind). Read
> `declared_providers` for the bindings. MCP follows the same rule (see
> [MCP](#mcp)).

### Capability versions

A capability can be published in more than one version (for example
`pdf.render@1` and `pdf.render@2`; see [pdf-render.md](pdf-render.md)).

- **Catalogue:** `GET /v1/capabilities` lists every version, in ascending
  order. The `latest` field is `true` on the highest version of each
  capability, the one a request without a version executes. Do not take the
  first entry for an `id` as the default.
- **Detail:** `GET /v1/capabilities/{capability_id}?version=N` returns version
  `N`; without `version`, the latest.
- **Execution:** `POST /v1/capabilities/{capability_id}:execute` takes an
  optional `version` form field. **Without it, the latest version runs**, so
  publishing a new version changes what unversioned clients receive: pin
  `version` when a client depends on a contract.
- **Planning:** `GET /v1/planning/domain` and
  `GET /v1/planning/capabilities/{capability_id}` (which also accepts
  `?version=`) describe the latest version unless asked otherwise.
- **MCP** exposes one tool per capability, which runs the latest version; MCP
  tools take no version (see [MCP](#mcp)).
- **CLI:** `acessilia-toolbox execute` takes `--capability-version N`. Binary
  outputs of by-reference capabilities are written next to the JSON document.

Long-running operations may require an external job/queue abstraction.
If asynchronous job state is exposed through the Toolbox, its
authoritative state must live in an external state service rather than
process memory.

## Authentication

The REST API requires Bearer token authentication with the key in the
`TOOLBOX_API_KEY` environment variable.

- **`TOOLBOX_API_KEY` set**: every endpoint **except `GET /v1/health`**
  requires an `Authorization: Bearer <token>` header matching the configured
  key. Tokens are compared in constant time.
- **`TOOLBOX_API_KEY` empty or unset**: the application **refuses to start**
  (`ConfigurationError`). A forgotten key used to leave every endpoint open.
- **Local development only**: set `TOOLBOX_ALLOW_UNAUTHENTICATED=1` to start
  without a key; every endpoint is then open and a warning is logged. Never set
  it on a shared or exposed host.

### Usage

``` bash
# Generate a key
openssl rand -hex 32

# Set it in .env
TOOLBOX_API_KEY=7a9b8c3d2e1f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8

# Authenticated request
curl -H "Authorization: Bearer 7a9b8c3d2e1f..." \
  http://localhost:8002/v1/capabilities

# Health check remains public
curl http://localhost:8002/v1/health
```

### Error responses

| Status | Code | Meaning |
|--------|------|---------|
| 401 | `authorization_failed` | Missing, malformed, or invalid token |

The OpenAPI document at `/v1/openapi.json` includes the security scheme
and can be explored interactively at `/v1/docs` (Swagger UI).

## VLM chat gateway

`POST /v1/vlm/chat/completions` relays an OpenAI chat-completions request to an
OpenAI-compatible VLM provider (`qwen3-vl-8b`). The agentic core points its
own OpenAI-compatible client here instead of at the model server, so that:

- its requests share the provider's `max_concurrency` limiter with the VLM
  capabilities, and the server never receives more requests than it has
  parallel slots;
- the model server's key stays with the Toolbox; the caller authenticates with
  `TOOLBOX_API_KEY`.

The body's `model` is the provider id; the Toolbox replaces it with the
provider's `config.model` and sends the provider's `api_key`. The response
streams as server-sent events only when the body sets `"stream": true`; any
other `stream` value is sent to the server as `false`.

The request waits for a free slot for at most the provider's
`slot_wait_seconds` (its `timeout_seconds` when unset), then gets
`timeout_seconds` at the server. A slot can stay taken for a whole long
generation, so the wait has its own, longer bound: the shipped `qwen3-vl-8b`
waits up to 1800 s, then gets 600 s.

**A client's own timeout must be longer than `slot_wait_seconds` +
`timeout_seconds`, with a margin.** At exactly the sum the client gives up
first and never reads the 504, and the wait can overrun its bound by up to
0.5 s (the polling interval). For `qwen3-vl-8b` that is more than 2400 s; the
core's `MODEL_CALL_TIMEOUT_SECONDS=2700` leaves 300 s.

The gateway takes a slot without queueing behind a capability already blocked
on the same limiter, so under heavy gateway load a capability may wait longer
for its turn. A client that disconnects, while waiting or
during the generation, frees its slot, and the upstream connection is closed
so the server stops generating.

Errors the gateway raises itself, an unexpected failure (500) included, have
the OpenAI shape, `{"error": {"message", "type", "code"}}`, with the Toolbox
error code (see [Errors](#errors)) in `type` and `code`; a 401 also carries
`WWW-Authenticate: Bearer`. Unlike the rest of the API, the gateway does not
use the Toolbox envelope: OpenAI-compatible clients such as Agno read the
message only from `error.message`, and reported the envelope as "Unknown model
error". The model server's errors are relayed as they are; llama-server and
vLLM answer in the OpenAI shape, but the gateway does not rewrite them.

| Status | Source | Meaning |
|--------|--------|---------|
| 400 | Toolbox | Body is not a JSON object with a string `model`, or the provider is not an OpenAI-compatible VLM |
| 401 | Toolbox | Missing or invalid `TOOLBOX_API_KEY` |
| 404 | Toolbox | `model` names no provider |
| 499 | Toolbox | The client disconnected; it never sees this status, which only appears in access logs |
| 502 | Toolbox | The model server rejected the Toolbox's own key |
| 500 | Toolbox | Unexpected failure in the gateway (logged with its traceback) |
| 503 | Toolbox | The model server is unreachable |
| 504 | Toolbox | No free slot within `slot_wait_seconds`, or no answer from the model server within `timeout_seconds` |
| any other | Model server | Relayed as is, for example a 400 for a request larger than the context window |

The core uses it with its `toolbox` client:

``` bash
AI_CLIENT=toolbox
TOOLBOX_BASE_URL=http://toolbox:8002
TOOLBOX_API_KEY=<TOOLBOX_API_KEY>
TOOLBOX_VLM_PROVIDER=qwen3-vl-8b
```

`GET /v1/vlm/health` (same key) probes every OpenAI-compatible VLM provider of
this Toolbox, concurrently: 200 with `"status": "healthy"` when all of them
answer, 503 with `"unhealthy"` otherwise, and each provider's `ProviderHealth`
in `providers`. A VLM provider whose adapter cannot be built (no
`config.model`, for instance) is unhealthy with the reason in `detail`, and a
Toolbox with no VLM provider at all answers 503.
A Toolbox whose `qwen3-vl-8b` points at another Toolbox's gateway
(`{endpoint}/v1/vlm`) reaches this route from its provider health check.

### Gateway mode

`acessilia_toolbox.api.gateway_app:create_gateway_app` starts the same package
with only `GET /v1/health` and the `/v1/vlm` routes, without `/v1/docs` or
`/v1/openapi.json`, in one process (its limiter is per process: no `--workers`).
Run next to a model server
shared by several environments, it is the server's one client, and every
environment's Toolbox points its VLM provider at it; see
[docs/tools.md](tools.md#vlm-gateway) and `deploy/vlm-gateway/`.

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
accessibility_mark
```

Example resources:

``` text
acessilia://capabilities
acessilia://providers
acessilia://planning/domain
```

The `acessilia://capabilities` resource returns each manifest with the same
`providers` (registered here) and `declared_providers` (bound by the manifest)
lists as the REST catalogue, plus the same `latest` flag, and each tool's
`provider` argument offers only the registered providers.

A capability published in several versions is a single tool that executes the
latest version; older versions are reachable through REST and the CLI only.
The planning resources likewise describe the latest version.

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
  --factory --host 0.0.0.0 --port 8002 --reload
```

The `--reload` flag watches for source changes and restarts automatically.

### Swagger UI

Once the server is running, open in a browser:

``` text
http://localhost:8002/v1/docs
```

Every endpoint can be exercised from the interactive Swagger UI.

### OpenAPI document

``` text
http://localhost:8002/v1/openapi.json
```

### smoke tests via curl

```bash
# Health check
curl http://localhost:8002/v1/health

# List capabilities
curl http://localhost:8002/v1/capabilities | python3 -m json.tool

# Capability detail (includes PDDL semantics)
curl http://localhost:8002/v1/capabilities/document.structure.extract | \
  python3 -m json.tool

# List registered providers
curl http://localhost:8002/v1/providers | python3 -m json.tool

# Provider health probe
curl http://localhost:8002/v1/providers/docling/health

# Extract a document
curl -X POST http://localhost:8002/v1/capabilities/document.structure.extract:execute \
  -F "file=@documento.pdf" \
  -F "language=pt-BR" | python3 -m json.tool | head -60

# Extract with a stored artifact (upload first, then reference by sha256)
curl -X POST http://localhost:8002/v1/artifacts \
  -F "file=@documento.pdf"
ARTIFACT_ID=$(curl -s -X POST http://localhost:8002/v1/artifacts \
  -F "file=@documento.pdf" | python3 -c "import sys,json;print(json.load(sys.stdin)['artifact_id'])")
curl -X POST "http://localhost:8002/v1/capabilities/document.structure.extract:execute" \
  -F "artifact_id=$ARTIFACT_ID" | python3 -m json.tool | head -30

# Retrieve a stored artifact
curl -o /dev/stdout http://localhost:8002/v1/artifacts/$ARTIFACT_ID > artifact.bin

# PDDL domain fragment
curl http://localhost:8002/v1/planning/domain

# PDDL predicates
curl http://localhost:8002/v1/planning/predicates

# PDDL action for a specific capability
curl http://localhost:8002/v1/planning/capabilities/document.structure.extract
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
curl -X POST http://localhost:8002/v1/capabilities/document.structure.extract:execute \
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
curl -X POST http://localhost:8002/v1/capabilities/document.structure.extract:execute \
  -F "file=@/tmp/teste.zip;type=application/zip"

# Unknown capability
curl http://localhost:8002/v1/capabilities/speech.synthesize

# Unknown provider
curl http://localhost:8002/v1/providers/mineru
```

### Stopping the server

Press `Ctrl+C` in the terminal running uvicorn, or close the terminal
session.

## MCP Manual Testing

The toolbox is also available over the Model Context Protocol (MCP) via
a stdio transport server.

### Starting the MCP server

```bash
python scripts/mcp_server.py
```

This starts listening for JSON-RPC 2.0 messages on stdin. Most MCP
clients communicate this way.

### Interactive mode

```bash
python scripts/mcp_server.py --interactive
```

This starts a REPL for manual exploration:

```
=== Acessilia Toolbox MCP Server ===
Server: acessilia-toolbox v0.1.0

=== Tools ===
  document_structure_extract
    Extract the logical and visual structure of a document...
    - file: File path to the document (or artifact_id for stored content)
    - media_type: MIME type of the file (e.g., 'application/pdf')
    - provider: Provider ID: docling
    - parameters: Provider-specific parameters as a JSON object

=== Resources ===
  acessilia://capabilities
    Available Capabilities: All capabilities exposed by the toolbox
  acessilia://providers
    Registered Providers: All registered providers and their capabilities
  acessilia://planning/domain
    PDDL Domain Fragment: PDDL domain fragment for planning
  acessilia://planning/predicates
    PDDL Predicates: Available predicates for planning
  acessilia://planning/capabilities/document.structure.extract
    PDDL Action for document.structure.extract: PDDL action fragment

=== Interactive Testing ===
Commands:
  tools                        List tools
  resources                    List resources
  read <uri>                   Read a resource
  call <tool> <json args>      Call a tool
  quit                         Exit
```

### Testing with curl (stdio transport)

Since MCP uses stdio, you can test by piping JSON-RPC requests:

```bash
# List tools
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \
  python scripts/mcp_server.py | python3 -m json.tool | head -30

# List resources
echo '{"jsonrpc":"2.0","id":1,"method":"resources/list","params":{}}' | \
  python scripts/mcp_server.py | python3 -m json.tool | head -20

# Read the PDDL domain
echo '{"jsonrpc":"2.0","id":1,"method":"resources/read",
  "params":{"uri":"acessilia://planning/domain"}}' | \
  python scripts/mcp_server.py | python3 -c "
import sys,json
print(json.load(sys.stdin)['result']['contents'][0]['text'])
"

# Extract a document (call a tool)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call",
  "params":{"name":"document_structure_extract",
  "arguments":{"file":"/tmp/sample-report.pdf","media_type":"application/pdf"}}}' | \
  python scripts/mcp_server.py | python3 -c "
import sys,json
r = json.load(sys.stdin)
for item in r['result']['content']:
    print(item['text'][:500])
"
```

### Testing with Claude Desktop or any MCP client

```bash
# Add to your Claude Desktop config (claude_desktop_config.json):
# {
#   "mcpServers": {
#     "acessilia-toolbox": {
#       "command": "python",
#       "args": ["/path/to/acessilia-toolbox/scripts/mcp_server.py"]
#     }
#   }
# }
```

Or use the `mcp` CLI tool:

```bash
pip install mcp
mcp run scripts/mcp_server.py
```

### Testing with the mcp CLI inspector

```bash
pip install mcp
mcp dev scripts/mcp_server.py
```

This opens an interactive inspector in the browser where you can browse
tools, call them, and inspect resources. Ensure `DOCLING_SERVE_URL` is
set in the environment before starting.

### Upload and capacity errors

- HTTP **413**: the request body exceeds the 201 MiB limit (200 MiB upload plus 1 MiB multipart overhead). Reduce the upload size.
- HTTP **503**: execution capacity is exhausted or a required provider/dependency is unavailable. Retry capacity failures after load decreases; check provider health and installation for unavailable dependencies.
