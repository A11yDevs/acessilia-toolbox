# Architecture Diagrams

## System context

``` mermaid
flowchart TB
    User[User] --> Core[Acessilia Agentic Core]
    Core --> Planner[PDDL Planner]
    Planner --> Core
    Core -->|REST / MCP| Toolbox[Acessilia Toolbox]

    Toolbox --> Docling[docling-serve]
    Toolbox --> MinerU[MinerU]
    Toolbox --> OCR[OCR Providers]
    Toolbox --> Pandoc[Pandoc]
    Toolbox --> Math[Math Services]
    Toolbox --> Validators[Accessibility Validators]
    Toolbox --> Storage[MinIO / S3]
    Toolbox --> Cache[Valkey / Redis]
```

**Text alternative:** The user interacts with the Agentic Core. The
Agentic Core controls the PDDL planner and invokes the Toolbox. The
Toolbox exposes deterministic providers including document extraction,
OCR, conversion, mathematics, validation, storage, and cache services.

## Internal boundaries

``` mermaid
flowchart LR
    subgraph AC[Agentic Core]
      G[Goals and Reasoning]
      PD[Problem Builder]
      DC[Domain Composer]
      PP[PDDL Planner]
      EX[Agent Executor]
      G --> PD
      G --> DC
      PD --> PP
      DC --> PP
      PP --> EX
    end

    subgraph TB[Acessilia Toolbox]
      REG[Capability Registry]
      API[REST / MCP]
      BIND[Provider Bindings]
      ART[Artifact Access]
      PSEM[PDDL Semantics]
      API --> REG
      API --> BIND
      API --> ART
      REG --> PSEM
    end

    EX --> API
    PSEM --> DC
```

**Text alternative:** The Agentic Core builds the problem and composes
its domain with PDDL semantics published by the Toolbox. Its planner
produces a plan for the Agent Executor. The executor calls REST/MCP.
Inside the Toolbox, a capability registry, provider bindings, artifact
access, and PDDL semantic publication remain passive services.

## State ownership

``` mermaid
flowchart TB
    Agent[Agentic Core] --> AgentDB[(Agent / Workflow State)]
    Toolbox[Stateless Toolbox] --> Store[(MinIO / S3 Artifacts)]
    Toolbox --> Op[(Valkey / Redis Operational State)]
    Agent --> Toolbox
```

**Text alternative:** Semantic and workflow state is owned by the
Agentic Core. The stateless Toolbox may access external artifact storage
and optional operational state services.
