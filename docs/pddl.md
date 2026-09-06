# PDDL Integration

## Why publish PDDL?

OpenAPI and MCP describe invocation and discovery. PDDL adds formal
composition semantics: preconditions, effects, state transitions, costs,
and goal reachability.

A useful mental model is:

``` text
OpenAPI = syntactic service contract
MCP     = agent/tool interaction contract
PDDL    = semantic composition contract
```

## Ownership

The Toolbox does **not** own planning.

| Planning element                  | Owner                          |
|-----------------------------------|--------------------------------|
| Capability predicates/actions     | Toolbox                        |
| Technical capability availability | Toolbox observation            |
| Agent workflow actions            | Agentic Core                   |
| Goal                              | Agentic Core                   |
| Semantic world state              | Agentic Core                   |
| Effective `domain.pddl`           | Agentic Core / Domain Composer |
| `problem.pddl`                    | Agentic Core                   |
| Planner invocation                | Agentic Core                   |
| Plan execution decisions          | Agentic Core                   |

## Toolbox publication

The Toolbox may expose:

``` text
/planning/predicates
/planning/capabilities/{id}
/planning/domain-fragments
/planning/domain.pddl
```

The monolithic domain endpoint is optional. Modular fragments are
preferred for composition.

Equivalent MCP resources may use URIs such as:

``` text
acessilia://capabilities
acessilia://planning/domain
acessilia://planning/capabilities/document.ocr
```

## Example action

``` lisp
(:action ocr
  :parameters (?d - document)
  :precondition
    (and
      (image-readable ?d)
      (scanned ?d))
  :effect
    (and
      (text-available ?d)
      (ocr-processed ?d)))
```

The action states what the capability requires and produces. It does not
decide whether OCR should be used.

## Effective domain

The Agentic Core may combine Toolbox fragments with an agent domain:

``` text
Toolbox Capability Domain
          +
Agent-specific Domain
          |
    Domain Composer
          |
     domain.pddl
```

Agent actions may include review, reflection, approval, escalation, or
human review. These do not belong in the Toolbox capability model unless
they are themselves external deterministic tools.

## Problem generation

The Agentic Core builds `problem.pddl` from observed facts and the
desired goal.

``` lisp
(define (problem accessible-doc)
  (:domain acessilia)
  (:objects doc1 - document)
  (:init
    (pdf doc1)
    (scanned doc1)
    (image-readable doc1))
  (:goal
    (and
      (accessible doc1)
      (html-available doc1)
      (validated doc1))))
```

Facts may originate from Toolbox inspection capabilities, but the
Agentic Core decides which facts enter its semantic state.

## Plan binding

Each PDDL action must map unambiguously to a capability identifier.

``` yaml
planning:
  action: extract-structure
execution:
  capability: document.structure.extract
```

The Agent Executor translates a plan step into a Toolbox invocation.

## What must not enter PDDL

Avoid encoding low-level infrastructure details such as ports,
credentials, bucket names, raw RAM percentages, HTTP retry counters, or
container IDs. Convert relevant infrastructure observations into stable
planning predicates only when they materially affect planning.

## Pipeline memory

Reusable pipelines belong to the Agentic Core or a workflow/pipeline
registry controlled by it. PDDL may generate or validate pipelines; the
Toolbox must not autonomously learn and select workflows.
