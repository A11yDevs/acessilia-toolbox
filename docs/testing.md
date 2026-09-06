# Testing

## Goals

Testing must prove that normalized capability contracts remain stable
even when providers change.

## Test layers

### Unit tests

No external provider required.

Cover:

- manifest/schema validation;
- capability registry behavior;
- request normalization;
- provider adapter mapping;
- PDDL fragment generation;
- PDDL action-to-capability binding;
- artifact identifiers and cache-key construction;
- error normalization.

### Contract tests

Each provider must pass the contract suite for every capability it
claims.

Current contract suites:

| Suite | File | Tested providers |
|---|---|---|
| `document.structure.extract` | `tests/contract/test_document_structure_extract.py` | docling |
| `artifact.store` + `artifact.retrieve` | `tests/contract/test_artifact_store.py` | minio (S3) |
| `cache.get` + `cache.put` | `tests/contract/test_cache.py` | valkey (Redis) |

All follow the same pattern: the `live_provider` (or `live_store` / `live_cache`)
fixture performs a health check with `pytest.skip()` if the provider is offline.
This allows tests to run both with and without containers.

``` text
Docling provider ----\
MinerU provider ------> document.structure.extract contract suite
MinIO (S3)      ------> artifact.* contract suite
Valkey (Redis)  ------> cache.* contract suite
Future provider -----/
```

A provider must not be registered as interchangeable if it violates the
normalized output contract.

### Integration tests

Run against real provider containers and verify:

- health probes;
- REST/MCP execution;
- artifact transfer by reference;
- object storage;
- timeouts and normalized failures;
- PDDL publication;
- cache behavior.

### Snapshot tests

Retain the useful strategy from `acessilia-structure-extractor`: compare
deterministic semantic outputs while excluding explicitly volatile
fields such as timestamps and execution IDs.

Dataset-backed tests should remain optional for fast unit-test
execution.

### Statelessness tests

Test that:

1.  request A can be handled by replica 1;
2.  replica 1 can be destroyed;
3.  request B referencing A's external artifact can be handled by
    replica 2;
4.  no authoritative workflow state is lost.

### Planning compatibility tests

Given capability manifests, generated PDDL must parse with the supported
planner/toolchain. Known domain/problem fixtures should produce valid or
explicitly unreachable planning results.

The planner itself belongs to the Agentic Core; Toolbox tests validate
published semantics, not agent strategy.

## Running

### Fast suite (no containers)

```bash
# All unit and integration tests (stub providers)
poetry run pytest tests/unit/ tests/integration/
```

### Contract suite (requires containers)

```bash
# Support providers (docling-serve, minio, valkey)
docker compose up -d

# Export environment variables from .env and run contract tests
export $(grep -v '^#' .env | xargs)
poetry run pytest tests/contract/ -v

# Tests that find a provider offline are silently skipped with pytest.skip()
```

### Full suite

```bash
docker compose up -d
export $(grep -v '^#' .env | xargs)
poetry run pytest
```

### Dependency table by test category

| Category | Required provider | Marker |
|---|---|---|
| `tests/unit/` | None | — |
| `tests/integration/` | None (stubs) | `integration` |
| `tests/contract/` | docling-serve | `contract` |
| `tests/contract/` | minio | `contract` |
| `tests/contract/` | valkey | `contract` |
| `tests/snapshot/` | docling-serve | `snapshot` |
| `tests/statelessness/` | minio + valkey | `statelessness` |

## CI expectations

Pull requests should run fast unit and schema tests. Provider
integration/snapshot tests may run in separate jobs with containers and
cached model assets.
