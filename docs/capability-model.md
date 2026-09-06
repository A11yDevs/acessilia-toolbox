# Capability Model

## Purpose

A capability is the normalized, provider-neutral description of a
deterministic operation that an agent may invoke through the Toolbox.

## Naming

Use hierarchical, stable identifiers:

``` text
artifact.identify
artifact.fingerprint
artifact.store
artifact.retrieve
document.ocr
document.structure.extract
document.layout.analyze
document.convert
pdf.inspect
pdf.render
table.extract
math.recognize
math.convert
math.render
accessibility.validate
speech.recognize
speech.synthesize
```

Provider names must not be embedded in capability IDs.

## Manifest

A future canonical manifest may contain:

``` yaml
id: document.ocr
version: 1
description: Extract machine-readable text from image-based document content.

input:
  schema: artifact/document@1

output:
  schema: artifact/document-text@1

execution:
  deterministic: true
  idempotent: true
  cacheable: true
  timeout_hint_seconds: 120

semantics:
  requires:
    - image_readable
  produces:
    - text_available

providers:
  - id: paddleocr
  - id: tesseract
```

## Semantics

`requires` and `produces` are not HTTP validation. They describe
planning-level world predicates.

The same manifest can feed:

``` text
Capability Manifest
  +-> REST/OpenAPI
  +-> MCP tool/resource metadata
  +-> PDDL action fragments
  +-> human documentation
  +-> contract tests
```

## Providers

A provider registration should include at least:

- provider ID and version;
- capabilities implemented;
- endpoint/transport;
- health endpoint or probe;
- supported media/formats;
- resource hints;
- provider-specific configuration schema.

Provider-specific fields must not leak into generic capability contracts
unless explicitly namespaced.

## Execution result

A normalized result should distinguish inline data from artifacts:

``` json
{
  "status": "succeeded",
  "capability": "document.ocr",
  "provider": "paddleocr",
  "artifacts": [
    {
      "artifact_id": "sha256:...",
      "media_type": "application/json"
    }
  ],
  "provenance": {
    "provider_version": "...",
    "parameters_hash": "sha256:..."
  }
}
```

## Capability compatibility

Two providers are interchangeable only when they satisfy the same
versioned capability contract. Similar purpose is not sufficient.
