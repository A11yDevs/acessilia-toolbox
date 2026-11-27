# Bounded text normalization (`document.text.normalize@1`)

Brazilian Portuguese: [pt-BR version](text-normalization.pt-br.md).

Provider `docstruct-text` executes **one pass** of the pinned Docstruct
`norm_fix` algorithm in the existing Toolbox process. It expands ligatures,
removes soft hyphens, replaces NBSP, joins eligible line-end hyphenation and
collapses duplicate spaces/tabs. Leading/trailing whitespace is preserved
unless that algorithm changes it. Text whose stripped prefix is `<table`,
`$$`, `\[` or `|` stays untouched. No marker insertion or deduplication occurs;
`text.postprocess@1` remains a separate, unchanged contract.

This is the provider side of [Acessilia #929](https://github.com/A11yDevs/acessilia/issues/929),
following accepted [ADR-0032](https://github.com/A11yDevs/acessilia/blob/develop/docs/adr/0032-toolbox-runtime-library-placement.md).
The Core retains target discovery, obligation state, metadata updates and
acceptance. The new `acessilia_toolbox.operations` package is internal and
released with the Toolbox; it imports neither Core nor SDK. The dependency
guard includes `acessilia_core`, `acessilia_sdk`, `backend` and `frontend`.

## Request and response

Send a UTF-8 JSON file to
`POST /v1/capabilities/document.text.normalize:execute`, with multipart
`provider=docstruct-text`, `version=1`, and the existing Bearer authentication.
There are no execution parameters. Input schema:
[`artifact/text-normalization-request@1`](../schemas/text-normalization-request@1.json).

```json
{"targets": [{"id": "paragraph-1", "text": "ﬁnal  text"}, {"id": "empty", "text": null}]}
```

The standard `CapabilityResult.document` contains `results`, `processed_count`
and `changed_count`. Each result has the corresponding `id`, normalized
`text`, `input_fingerprint`, `processed` and `changed`. Output schema:
[`artifact/text-normalization-result@1`](../schemas/text-normalization-result@1.json).

- Exactly one result per target, in request order; distinct ids may carry the
  same text. Duplicate target ids are rejected before any transformation.
- `null` and `""` are preserved with both flags false. Any nonempty text,
  including whitespace or protected content, is processed; unchanged text has
  `changed=false`. Counts sum those flags; a zero count does not decide whether
  a Core obligation is satisfied.
- `input_fingerprint` is `sha256:` plus the hex digest of UTF-8 canonical JSON
  of **the original** `{"id": id, "text": text}`: keys sorted, separators
  `(',', ':')`, `ensure_ascii=False`, no newline. Null and empty remain distinct.
- Consumers must validate unique/matching ids, counts, flags, capability/provider
  versions and input fingerprints before applying observations. The Core then
  checks that the processed text/context is still applicable; a stable id alone
  does not authorize overwriting a newer revision. Apply `norm_fixed=true` only
  for a changed target, preserving unrelated metadata, as the old handler did.
- Additional fields, planning manifests, non-string texts/ids, duplicate JSON
  keys, malformed UTF-8, lone surrogates and non-finite constants are rejected.
  Model validators enforce unique ids and consistent result counts beyond the
  structural JSON Schema constraints.

Validated target/result collections are immutable tuple snapshots internally;
their elements and count fields are frozen too. Public JSON continues to use
arrays. Only input targets expose the internal input-fingerprint helper;
observations carry the original `input_fingerprint` separately from output text.

Schemas are generated from the strict Pydantic operational DTOs, not from a
copy of the Core planning envelope:

```bash
python scripts/export_text_normalization_schemas.py
python scripts/export_text_normalization_schemas.py --check
python scripts/export_contract.py
python scripts/derive_providers_config.py --check
```

## Limits, repetition and lifecycle

- At most **256 targets**, ids of **1–256 characters**, and **1,048,576 bytes**
  for the entire serialized request. The adapter checks the byte limit before
  JSON decoding; target count/id length are model checks after parsing. The
  generic executor may already have fingerprinted/materialized the input under
  its global limits. Thus 1 MiB is neither an end-to-end memory ceiling nor a
  total response-size limit; upload/spooling/global memory limits still apply.
- The adapter parsing/computation budget/hint is **5 seconds**, or a lower provider timeout.
  The adapter checks elapsed time after bounded computation and refuses an
  over-budget result with `provider_timeout`; it cannot preempt synchronous
  Docstruct code. Admission uses the existing process limit (default four
  executions, `TOOLBOX_MAX_CONCURRENT_EXECUTIONS`), not a new pool or service.
- Repeating the **same input** is deterministic and has no document/workflow
  side effects. Retry the immutable original request after a lost response;
  do not normalize the returned text again as a retry. One-pass normalization
  is not a fixed-point promise: `a-\nb-\nc` becomes `ab-\nc` in Docstruct 0.5.8.
- Job ids, operation ids, input digests and cache keys have distinct roles.
  Use a new operation id when input/provider/version changes. Existing operation
  joining is process-local and may be evicted/restarted; it is not durable
  distributed idempotency. The existing join does not validate the full input
  identity ([Toolbox #173](https://github.com/A11yDevs/acessilia-toolbox/issues/173));
  callers must not reuse an id for changed work or skip fingerprint checks.
  Cancellation is observed by the existing executor
  before/after provider execution and before publication; acknowledgment is
  not synchronous preemption. A cancel observed at those checks prevents
  subsequent publication work; it does not roll back writes already started
  or completed between checks. Storage and cache publication are not one
  atomic transaction. The adapter budget does not bound upload, generic input
  fingerprinting/materialization, cache/storage or response construction.
- Provenance records the original payload fingerprint, exact capability/provider
  version, Toolbox runtime and Docstruct/algorithm versions; those component
  versions also enter the existing execution cache key. No hidden semantic
  provider substitution is added.
- Results use the existing inline JSON/artifact/cache path. No binary artifacts
  or private stores are created. Configured artifact/cache retention remains
  deployment-owned; storage-outage behavior is the executor's existing behavior.
- Validation failures use `invalid_input` (400), byte overflow
  `upload_too_large` (413), wrong media `unsupported_media_type` (415), unsupported
  adapter identity/version or algorithm failure `provider_execution_failed` (502),
  and an observed expired budget `provider_timeout` (504). No partial batch is
  published on an adapter failure.

## Migration order and evidence

Merge the dependency guard/contract/provider before publishing a Toolbox image
and switching the Core. The Core integration must bind this version, preserve
old behavior/checkpoints, reject stale/malformed responses, shrink the `norm-fix`
allowance and retain explicit remote failure. An older Toolbox remains a
version/capability failure, without a local computational fallback. Keep old
capabilities available for old consumers and rollback.

Provider tests exercise the real dispatch/executor/REST path, algorithm cases,
batch boundaries, input rejection, cache/provenance and cancellation before
storage/cache. Full Core differential and checkpoint evidence belongs to the
consumer integration; this provider PR alone does not migrate the method.
Measurements of batching and bytes are local evidence only, not a production
latency or quality claim.
