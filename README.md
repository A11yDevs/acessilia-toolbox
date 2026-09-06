# Acessilia Toolbox

**A stateless, composable toolbox for exposing deterministic
capabilities to agentic systems via REST and MCP.**

Acessilia Toolbox is the deterministic capability layer of the Acessilia
ecosystem. It provides a stable, provider-neutral interface to
document-processing, accessibility, storage, conversion, validation,
speech, and related services while keeping reasoning, planning, workflow
state, and strategic decisions in the agentic system.

## Core ideas

- **Tools are controlled; they do not control the agent.**
- **Stateless core:** workflow, session, semantic, and decision state
  stay outside the Toolbox.
- **Capability-oriented:** agents request stable capabilities rather
  than coupling to vendor-specific APIs.
- **Provider-independent:** Docling, MinerU, OCR engines, Pandoc, MinIO,
  validators, and future services are replaceable providers.
- **REST + MCP:** REST supports conventional service integration; MCP
  exposes tools and resources to agents.
- **PDDL semantics:** the Toolbox may publish formal capability
  semantics (preconditions/effects) for planners, but planning remains
  in the Agentic Core.
- **External persistence:** artifacts may live in MinIO/S3/filesystem
  and operational cache in Valkey/Redis without making the Toolbox
  itself stateful.

## Conceptual architecture

``` mermaid
flowchart TB
    U[User] --> A[Agentic Core]
    A --> P[PDDL Planner]
    P --> A
    A -->|REST / MCP| T[Acessilia Toolbox]
    T --> D[Docling / docling-serve]
    T --> M[MinerU]
    T --> O[OCR Providers]
    T --> X[Pandoc / Math Tools]
    T --> S[MinIO / Object Storage]
    T --> C[Cache Provider]
    T --> V[Accessibility Validators]
```

The Agentic Core owns goals, reasoning, PDDL problems, pipeline
composition, reflection, provider policy, and execution decisions. The
Toolbox describes and executes capabilities.

## Documentation

Start with [docs/index.md](docs/index.md).

Key documents:

- [Architecture](docs/architecture.md)
- [Project Constitution](docs/constitution.md)
- [Capability Model](docs/capability-model.md)
- [PDDL Integration](docs/pddl.md)
- [API and Protocols](docs/api.md)
- [Tools and Providers](docs/tools.md)
- [Installation](docs/installation.md)
- [Testing](docs/testing.md)
- [Contributing](docs/contribution.md)

## Status

This repository defines a new architecture derived from lessons learned
in `acessilia-structure-extractor`. Interfaces and schemas described
here are architectural targets unless explicitly marked as implemented.

## License

The project license should be declared in the repository `LICENSE` file.
Provider services retain their own licenses.
