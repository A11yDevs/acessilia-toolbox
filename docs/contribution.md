# Contribution

Thank you for contributing to Acessilia Toolbox.

## Workflow

``` text
Fork -> Feature branch -> Development -> Tests -> Pull request -> Review -> Merge
```

Suggested branch prefixes:

| Prefix      | Purpose       |
|-------------|---------------|
| `feat/`     | Feature       |
| `fix/`      | Bug fix       |
| `docs/`     | Documentation |
| `refactor/` | Refactoring   |
| `test/`     | Tests         |
| `chore/`    | Maintenance   |

## Development rules

Contributions should preserve the project constitution:

- do not move agent reasoning or goals into the Toolbox;
- do not add mandatory heavy provider dependencies to the core;
- add capabilities through normalized contracts;
- keep provider-specific behavior behind adapters;
- keep authoritative persistent state external;
- update PDDL semantics when capability semantics change;
- add contract tests for new providers;
- document breaking changes.

## Commit messages

Conventional Commits are recommended:

``` text
feat: add OCR capability contract
fix: normalize provider timeout errors
docs: document PDDL capability semantics
test: add Docling contract snapshots
```

## Pull requests

A pull request should explain:

1.  what changed;
2.  why it belongs in the Toolbox rather than Agentic Core;
3.  which capability/provider contract changes;
4.  compatibility impact;
5.  tests performed;
6.  documentation updates.

## Adding a capability

A new capability should include:

- stable ID and version;
- input/output schema;
- determinism/idempotency/cacheability metadata where relevant;
- semantic preconditions/effects if useful for planning;
- REST/MCP exposure;
- tests;
- documentation.

## Adding a provider

A provider should not require consumers to change. It must declare the
capability versions it implements and pass their contract tests.

## Documentation accessibility

Use logical heading levels, descriptive links, real lists, plain
language, and textual explanations for complex diagrams. Do not encode
essential information only visually.
