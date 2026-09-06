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

## Provider isolation

Do not install Docling, MinerU, OCR ML stacks, or object-storage servers
as mandatory Toolbox dependencies. Run heavy providers separately.

Example conceptual topology:

``` text
toolbox:8000
docling-serve:5001
mineru:8001
minio:9000
valkey:6379
```

Provider endpoints should be configuration, not source-code constants.

## Configuration

Prefer environment variables or declarative provider manifests. Secrets
must come from a secret manager or deployment environment, never
committed configuration.

Example:

``` yaml
providers:
  - id: docling
    transport: http
    endpoint: http://docling-serve:5001
    capabilities:
      - document.structure.extract

  - id: object-storage
    transport: s3
    endpoint: http://minio:9000
    capabilities:
      - artifact.store
      - artifact.retrieve
```

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
