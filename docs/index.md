# Acessilia Toolbox Documentation

Acessilia Toolbox is a stateless capability gateway for deterministic
tools used by Acessilia agents. It generalizes the dependency-isolation
approach of the former Structure Extractor into a provider-neutral
toolbox.

## Documentation map

| Document                                             | Purpose                                                             |
|------------------------------------------------------|---------------------------------------------------------------------|
| [architecture.md](architecture.md)                   | System boundaries, components, state ownership, and execution model |
| [constitution.md](constitution.md)                   | Non-negotiable architectural principles                             |
| [capability-model.md](capability-model.md)           | Normalized capability and provider contracts                        |
| [pddl.md](pddl.md)                                   | PDDL publication and consumption model                              |
| [api.md](api.md)                                     | REST and MCP interface principles                                   |
| [artifacts.md](artifacts.md)                         | Artifact references, object storage, hashing, and cache             |
| [tools.md](tools.md)                                 | Candidate capabilities and providers                                |
| [installation.md](installation.md)                   | Development and deployment guidance                                 |
| [testing.md](testing.md)                             | Testing strategy                                                    |
| [contribution.md](contribution.md)                   | Contribution workflow                                               |
| [uml/architecture.md](uml/architecture.md)           | Architecture diagrams                                               |
| [uml/use_cases.md](uml/use_cases.md)                 | Use cases                                                           |
| [uml/sequence_diagrams.md](uml/sequence_diagrams.md) | Sequence diagrams                                                   |

## Design summary

The Toolbox answers four questions:

1.  **What deterministic capabilities are available?**
2.  **What are their machine-readable contracts and planning
    semantics?**
3.  **How can an authorized caller execute one of them?**
4.  **What is their current technical availability?**

It deliberately does **not** answer:

- What goal should the system pursue?
- Which semantic strategy should be selected?
- Which pipeline should be constructed?
- When should reflection or human review occur?
- What should be remembered about a workflow?

Those responsibilities belong to the Agentic Core.
