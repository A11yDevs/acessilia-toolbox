# Project Constitution

## Identity

**Name:** Acessilia Toolbox  
**Purpose:** Stateless, composable exposure of deterministic
capabilities to agentic systems.  
**Primary interfaces:** REST and MCP.  
**Planning interoperability:** PDDL capability semantics.

## Objective

Provide a small, replaceable boundary between agentic intelligence and
deterministic infrastructure. Heavy or specialized services must be
independently deployable, loadable, unloadable, replaceable, and
scalable without creating direct dependencies in the Toolbox Core or
Agentic Core.

## Principles

### 1. Agent sovereignty

The Agentic Core owns goals, reasoning, strategy, workflow composition,
planning, reflection, and semantic decisions.

**Rule:** *The Toolbox executes actions; the Agentic Core chooses
actions.*

### 2. Stateless Toolbox Core

The Toolbox shall not persist workflow, session, semantic, planning, or
decision state. Restarting a Toolbox instance must not destroy
authoritative application state.

Permitted ephemeral state includes connection pools, short-lived health
caches, metrics buffers, and circuit-breaker state.

### 3. Capability before provider

Public contracts should use stable capability identifiers such as:

- `document.ocr`
- `document.structure.extract`
- `document.convert`
- `math.recognize`
- `accessibility.validate`
- `artifact.store`

Provider names such as Docling, MinerU, Pandoc, or MinIO are
implementation choices.

### 4. Provider isolation

The core must not import heavy provider libraries. Providers should
normally be remote services, subprocess adapters, plugins with strict
boundaries, or independently deployable containers.

### 5. No hidden semantic routing

The Toolbox may perform technical routing among equivalent replicas,
retries, health-based failover, or cache lookup. Semantic choices that
affect quality, meaning, workflow, or accessibility strategy remain
under agent control.

### 6. External state ownership

- Agent/workflow state → Agentic Core or its persistence layer.
- Artifact state → object storage such as MinIO/S3/filesystem.
- Operational cache/locks → external services such as Valkey/Redis when
  needed.
- Toolbox → stateless facade and execution boundary.

### 7. Multiple interfaces, one capability contract

REST, MCP, documentation, validation schemas, and PDDL should derive
from the same normalized capability metadata whenever practical.

### 8. PDDL describes; it does not control

The Toolbox may publish PDDL fragments describing capability
preconditions and effects. It shall not define agent goals or construct
`problem.pddl`.

### 9. Reproducibility and provenance

Execution results should carry enough provenance to identify input
fingerprints, capability version, provider, provider version,
parameters, and generated artifacts.

### 10. Accessibility by design

Documentation, APIs, error messages, artifacts, and generated interfaces
should follow accessibility best practices. Complex diagrams must have
textual explanations.

### 11. Secure handling of untrusted artifacts

Uploads are untrusted input. File identification, limits, sandboxing,
scanning where appropriate, and least-privilege provider execution
should be supported.

### 12. Evolution through versioned contracts

Capability schemas and artifact contracts must be versioned. Breaking
changes require explicit major-version transitions.

## Language and contribution conventions

### English-first, with Brazilian Portuguese as the official secondary language

English is the mandatory, authoritative language of the project. All source
code — identifiers, comments, docstrings, and log messages — as well as
Markdown documentation, issues, pull requests, and commit messages must be
written in English.

Brazilian Portuguese (pt-BR) is the official secondary language. Producing
pt-BR documentation is desirable for all documentation. Every `*.pt-br.md`
mirror must be updated in the same pull request whenever its English source
changes, and the English version is authoritative in case of divergence.

### Internationalization

Translation and support for other languages is an important secondary goal,
aimed at a broad and inclusive audience. Localized content in other languages
is welcome but never blocks a merge.

User-facing content — Telegram messages, rendered output, and prompts for AI
models — must be localized according to the user's locale, covering English
and Brazilian Portuguese and extending to other languages as the
internationalization effort grows.

Educational materials are a priority domain for internationalization: they
should be produced in the audience's language whenever possible, with English
and Brazilian Portuguese as the primary supported languages.

### Contribution rules

- Mandatory type hints for Python code where Python is used.
- Conventional Commits are recommended.
- Tests and documentation accompany behavior changes.
- Documentation published as a `*.pt-br.md` mirror must have a sibling English
  source; the mirror is never the only copy.
