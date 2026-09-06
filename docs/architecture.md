# Architecture

## Overview

Acessilia Toolbox generalizes the original Acessilia Structure
Extractor's dependency-isolation model. The former project isolated
Docling and exposed a canonical Processing Manifest; the Toolbox expands
that idea into a generic deterministic capability layer.

The key boundary is **intelligence versus tools**, not AI versus non-AI.
A deterministic PDDL planner still performs strategic plan selection and
therefore belongs to the Agentic Core. A deterministic converter or
extractor belongs behind the Toolbox.

## Responsibilities

### Agentic Core

Owns:

- user intent and goals;
- semantic world state;
- `problem.pddl`;
- agent-specific PDDL domain knowledge;
- PDDL planner invocation;
- plan-and-execute;
- reflection and human-in-the-loop decisions;
- pipeline construction and memory;
- strategic provider selection.

### Toolbox

Owns:

- capability discovery;
- normalized execution contracts;
- REST and MCP exposure;
- provider adapters;
- technical health information;
- technical retries/timeouts;
- artifact transport by reference;
- cache access;
- optional provider lifecycle commands when explicitly requested;
- publication of capability PDDL semantics.

### Providers

Examples include docling-serve, MinerU, OCR engines, Pandoc,
mathematical conversion/rendering tools, MinIO, cache services, speech
services, and accessibility validators.

Providers own their implementation-specific dependencies.

## Statelessness

A Toolbox request must contain, or reference, everything needed for
execution. Persistent state belongs outside the Toolbox.

``` text
Agentic state     -> Agentic Core persistence
Artifact state    -> MinIO / S3 / filesystem
Operational state -> Valkey / Redis / queue (optional)
Toolbox process   -> ephemeral only
```

This permits horizontal scaling and replacement of Toolbox instances.

## Capability model

The public abstraction is:

``` text
Capability
  -> semantic contract
  -> one or more Providers
  -> REST/MCP bindings
  -> optional PDDL semantics
```

A capability describes *what can be done*. A provider describes *how it
is implemented*.

Example:

``` yaml
id: document.structure.extract
version: 1
input:
  type: artifact/document
output:
  type: artifact/structured-document
semantics:
  requires: [text_available]
  produces: [structured, has_ast]
providers:
  - docling
  - mineru
```

## Provider selection boundary

The Toolbox may automatically choose among technically equivalent
replicas. It should not silently choose between semantically meaningful
alternatives.

The caller may specify:

``` json
{
  "capability": "document.structure.extract",
  "provider": "docling"
}
```

or explicitly delegate:

``` json
{
  "capability": "document.structure.extract",
  "provider": "auto"
}
```

`auto` must have a documented policy.

## PDDL boundary

Conceptually, planning uses three inputs:

1.  **Toolbox Capability Domain** — PDDL fragments generated from
    capability semantics.
2.  **Agent Domain** — workflow and agent-specific actions/rules.
3.  **Problem** — current objects, observed state, constraints, and
    goal.

The Agentic Core composes (1) and (2) into the effective `domain.pddl`.
It generates `problem.pddl`. The planner remains controlled by the
Agentic Core.

``` text
Toolbox capability fragments + Agent domain
                    |
              Domain Composer
                    |
               domain.pddl

Observed state + Agent goal
                    |
              Problem Builder
                    |
              problem.pddl

domain.pddl + problem.pddl -> Planner -> Plan -> Agent Executor -> Toolbox
```

## Artifact flow

Large payloads should be passed by reference when possible:

``` text
Upload -> object.store -> artifact_id
Agent -> capability.execute(artifact_id)
Provider -> output
Toolbox -> object.store(output) -> output_artifact_id
```

A content fingerprint such as SHA-256 can support deduplication,
provenance, and deterministic caching.

## Relationship to the former Structure Extractor

Compatible ideas retained:

- isolation of heavy dependencies;
- independent service scalability;
- backend abstraction;
- REST/MCP intent;
- canonical, versioned contracts;
- snapshot/integration testing;
- Docling through docling-serve as an external provider.

Changed ideas:

- the Toolbox is generic rather than extraction-specific;
- no Docling-specific object is a core contract;
- Processing Manifest becomes one possible artifact/schema rather than
  the Toolbox's sole contract;
- providers are capabilities rather than hard-coded extractor classes;
- persistent workflow state is explicitly outside the Toolbox;
- PDDL semantics may be published for composition while planning stays
  outside.
