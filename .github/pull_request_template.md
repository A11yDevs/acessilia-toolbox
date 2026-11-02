<!--
Write the pull request in English (constitution, "Language and contribution
conventions"). Delete the sections that do not apply; keep the checklist.
-->

## Why

<!-- The problem this solves, and the issue it belongs to. -->

Part of #

<!--
"Closes #N" only closes the issue when the PR merges into the default branch
(main). For a PR into develop, close the issue by hand after the merge.
-->

## What changes

<!-- What changed, and anything a reviewer could not guess from the diff. -->

## Verification

<!-- How you checked it: tests, a sidecar smoke test, a real call to the API. -->

## Conformance

The Toolbox principles are in [docs/constitution.md](https://github.com/A11yDevs/acessilia-toolbox/blob/develop/docs/constitution.md); the ecosystem principles (P1–P9) and the cross-component decisions are in the core's [constitution](https://github.com/A11yDevs/acessilia/blob/develop/docs/constitution.md) and [ADRs](https://github.com/A11yDevs/acessilia/blob/develop/docs/adr/README.md). Check each item, or explain under it why it does not apply.

- [ ] **The Toolbox executes, the core decides (principles 1 and 5, P2).** No planning, workflow or semantic choice was added here.
- [ ] **One implementation (P1).** Pure structural algorithms come from docstruct; nothing here copies code that docstruct, the core or another provider already has.
- [ ] **Dependencies (P3).** No import of `acessilia_sdk` or of the core; the only ecosystem dependency is docstruct, at a pinned version.
- [ ] **Stateless (principles 2 and 6).** No workflow, session or decision state is kept in the process; artifacts go to the artifact store and travel as references (P5).
- [ ] **Capability before provider (principles 3 and 4).** Callers see a capability; heavy libraries run in a provider or sidecar, not in the Toolbox core.
- [ ] **Versioned contracts and provenance (principles 9 and 12, P7, P9).** A change to a capability, schema or `ArtifactRef` is a new version, results carry provider and version provenance, and the core's SDK and contract fixture are notified when the catalogue changes.
- [ ] **Untrusted input (principle 11).** Uploads and payloads are validated, bounded and sandboxed where a tool could read or execute them.
- [ ] **Sidecars.** A new or changed sidecar image is built and published by the `Sidecars` workflow, and the provider and model versions it reports enter the execution cache key (`compute_cache_key`), so an upgrade does not reuse old results.
- [ ] **Decision recorded.** A change of responsibilities, dependencies, execution mode or public contract has an ADR in the core repository, or a link to an accepted one.

## Quality

- [ ] `pytest` passes, and new behaviour has tests.
- [ ] `ruff check src/ tests/` passes.
- [ ] Every changed `.md` file that has a `.pt-br.md` mirror has the mirror updated in this pull request.
