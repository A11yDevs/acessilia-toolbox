# Sequence Diagrams

## Capability discovery and planning

``` mermaid
sequenceDiagram
    participant A as Agentic Core
    participant T as Toolbox
    participant P as PDDL Planner

    A->>T: List capabilities + planning semantics
    T-->>A: Capability manifests / PDDL fragments
    A->>A: Observe state and define goal
    A->>A: Compose domain.pddl
    A->>A: Generate problem.pddl
    A->>P: domain.pddl + problem.pddl
    P-->>A: Plan
```

The Toolbox supplies formal descriptions. The Agentic Core supplies the
problem and goal and controls the planner.

## Execute a document capability

``` mermaid
sequenceDiagram
    participant A as Agentic Core
    participant T as Toolbox
    participant D as docling-serve
    participant S as Object Storage

    A->>T: execute document.structure.extract(artifact_id, provider=docling)
    T->>S: retrieve input artifact
    S-->>T: artifact stream/reference
    T->>D: provider-specific request
    D-->>T: structured result
    T->>S: store normalized output
    S-->>T: output artifact_id
    T-->>A: normalized result + artifact_id + provenance
```

## Cache hit

``` mermaid
sequenceDiagram
    participant A as Agentic Core
    participant T as Toolbox
    participant C as External Cache
    participant P as Provider

    A->>T: execute capability
    T->>C: get(cache_key)
    C-->>T: cached artifact reference
    T-->>A: cached normalized result
```

The provider is not started or called on a valid cache hit.

## Cache miss

``` mermaid
sequenceDiagram
    participant A as Agentic Core
    participant T as Toolbox
    participant C as External Cache
    participant P as Provider

    A->>T: execute capability
    T->>C: get(cache_key)
    C-->>T: miss
    T->>P: execute
    P-->>T: result
    T->>C: put(cache_key, result reference)
    T-->>A: normalized result
```

## Stateless replica replacement

``` mermaid
sequenceDiagram
    participant A as Agentic Core
    participant T1 as Toolbox Replica 1
    participant S as External Storage
    participant T2 as Toolbox Replica 2

    A->>T1: store/process artifact
    T1->>S: persist artifact
    S-->>T1: artifact_id
    T1-->>A: artifact_id
    Note over T1: Replica can terminate
    A->>T2: execute using artifact_id
    T2->>S: retrieve artifact
    S-->>T2: artifact
    T2-->>A: result
```
