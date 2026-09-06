# Use Cases

## Actors

- **Agentic Core:** primary consumer and decision maker.
- **Developer/operator:** configures providers and observes health.
- **Provider:** external deterministic implementation.
- **Object storage/cache:** external state services.

## Use cases

``` mermaid
flowchart LR
    Agent[Agentic Core] --> UC1[Discover capabilities]
    Agent --> UC2[Read capability semantics]
    Agent --> UC3[Execute capability]
    Agent --> UC4[Store/retrieve artifact]
    Agent --> UC5[Request provider explicitly]

    Operator[Operator] --> UC6[Register/configure provider]
    Operator --> UC7[Inspect technical health]

    UC3 --> Provider[Deterministic Provider]
    UC4 --> Storage[External Storage]
```

### UC-01 Discover capabilities

The Agentic Core obtains normalized capability identifiers, schemas,
versions, providers, and availability.

### UC-02 Consume PDDL semantics

The Agentic Core obtains PDDL fragments or normalized semantic metadata,
composes them with its own domain, generates `problem.pddl`, and invokes
its planner.

### UC-03 Execute a capability

The Agentic Core chooses an action and calls the Toolbox. The Toolbox
validates the request, invokes the requested or explicitly delegated
provider, normalizes the result, and returns it.

### UC-04 Process artifacts by reference

The caller stores an artifact, receives an immutable reference, and
reuses that reference across capabilities without repeatedly
transporting large blobs.

### UC-05 Use cached deterministic output

When cache use is permitted, the Toolbox checks an external cache keyed
by content, capability, provider/version, and parameters. A hit avoids
unnecessary provider execution.

### UC-06 Add a provider

An operator adds a provider manifest. The provider becomes discoverable
only after configuration validation, health checks, and capability
contract verification.

### UC-07 Replace a provider

A provider may be upgraded or replaced without changing agent-facing
capability contracts, provided compatibility is preserved.

### UC-08 Validate accessibility

An agent invokes a deterministic validator. The Toolbox returns
findings. The agent decides whether to repair, replan, escalate, or
request human review.

## Explicit non-use cases

The Toolbox does not autonomously:

- choose user goals;
- construct semantic workflows;
- run reflection loops;
- decide when human review is required;
- maintain agent memory;
- create `problem.pddl`.
