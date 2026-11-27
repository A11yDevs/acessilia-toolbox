# Operation request identity

Brazilian Portuguese: [pt-BR version](operation-identity.pt-br.md).

An `operation_id` names one logical execution within a task. Reusing it joins
the original response or execution error only when the complete request matches.
A conflicting validated request returns the existing `400 invalid_input` error;
malformed or unsupported requests keep their normal validation errors. A different
task receives `404 operation_not_found`; authentication is checked first.
`attempt_id` changes on each HTTP attempt and does not affect request identity.
Calls without operation headers retain their existing independent execution path.

This enforces the retry invariant of accepted
[ADR-0019](https://github.com/A11yDevs/acessilia/blob/develop/docs/adr/0019-operation-identifiers-and-cancellation.md)
and fixes [#173](https://github.com/A11yDevs/acessilia-toolbox/issues/173).
It adds no capability version, public field, error code, storage or workflow
state. The process-local registry retains only a private digest alongside the
existing outcome; it exposes neither the digest nor its source material.

## What must match

| Field | Matching rule |
|---|---|
| Route | Artifact upload and capability execution are distinct, even for `artifact.store`. |
| Capability | Resolved id/version and manifest binding data. |
| Provider | Resolved descriptor, including version and configuration. |
| Uploaded input | Exact content bytes, effective filename and media type. |
| Referenced input | Canonical `sha256:` id; the equivalent bare digest matches. |
| Parameters | Parsed JSON object, sorted keys and compact UTF-8 encoding; object key order and whitespace do not matter. |
| Language/cache | Effective language and `no_cache`, including defaults. |

The resolved provider id and capability version are passed to execution, so a
default is selected before binding. A retry that resolves different defaults or
configuration is refused. JSON parameter types and array order remain distinct;
non-finite numbers and invalid UTF-8 strings are refused. An uploaded JSON file
is still compared as bytes: reformatting that file changes the request. Filename
and media type matter because providers can observe them.

Artifact retries compare the caller's immutable reference without downloading
it again. Filename/media type resolved from storage belong to the original
execution and retained outcome. A storage outage or later metadata change does
not prevent joining that outcome; a different artifact id does. Upload and
artifact-reference modes are different requests even if their bytes coincide.

## Admission, concurrency and failures

The owner reserves the id before preparing input, preserving cancellation during
preparation. Its validated request digest is sealed before execution. A retry
prepares its own identity, waits for that seal if needed, compares it and only
then joins the outcome. Mismatches do not modify or cancel the owner.

Only the owner acquires an execution slot. Waiting for the owner's identity or
outcome uses asynchronous notifications, leaving the worker pool available for
hashing, execution and cancellation. Finishing or releasing an operation wakes
both kinds of waiter, including when preparation fails before a digest exists.

A refusal before handoff to the executor or artifact store releases the id and
slot: for example invalid parameters, unavailable input storage or exhausted
capacity. Already-waiting retries receive that refusal; a later attempt may
become the owner. Once handed off, success or failure is retained because work
may have occurred. An identical retry receives that outcome; a changed retry is
refused. Cancellation keeps the existing cooperative semantics and terminal id;
it cannot turn a cancelled operation into new work.

## Limits and consumer responsibilities

Every named upload attempt is hashed, including a retry, to verify the bytes
actually supplied. Reads are positive, at most 1 MiB each, stop after the existing
REST upload limit plus one byte, and rewind the request-owned stream. This adds
one sequential pass per attempt, without another full in-memory copy or artifact
download. Existing HTTP/spooling limits still apply; joining does not bound the
number of incoming HTTP requests. Request binding is separate from execution
cache/provenance keys and does not change their formats.

Matching is available only while the owning replica retains the entry. Eviction,
restart and independent replicas remain outside this guarantee; there is no
durable distributed idempotency. Callers must retry the immutable original
request, use a new operation id for changed work, and validate response contract,
provenance and applicability before applying results. In particular,
[text normalization](text-normalization.md) consumers still check per-target input
fingerprints and the current document revision.
