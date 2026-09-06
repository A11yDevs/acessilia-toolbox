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
