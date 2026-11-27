# Operation request identity

Brazilian Portuguese: [pt-BR version](operation-identity.pt-br.md).

An `operation_id` names one logical execution within a task. Reusing it joins
the original response or execution error only when the complete request matches.
Against a retained, sealed identity, a conflicting validated request returns the
existing `400 invalid_input` error; malformed or unsupported requests keep their
normal validation errors. A different task receives `404 operation_not_found`;
authentication is checked first.
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

Registries are loaded when the application is created and remain stable during
requests. There is no supported concurrent hot reload. Pinning the provider id
and capability version does not make mutable configuration a snapshot; changes
require a new application/deployment. Tests that change configuration between
requests exercise drift detection, not a supported mutation during execution.

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
capacity. Retries attached before the identity was sealed can receive that
preparation refusal regardless of their input. Once released, a later request
can become the owner and needs a new slot. Once handed off, success or failure is
retained because work may have occurred. An identical retry receives that outcome;
a changed retry is refused. Cancellation keeps the existing cooperative semantics and terminal id;
it cannot turn a cancelled operation into new work.

The terminal transition under the registry lock determines the owner's response
as well as retries: cancellation that wins returns `409 operation_cancelled` and
retains no result; a committed success survives a later cancel request. This does
not undo storage/cache publication already started before cancellation.

HTTP coroutine interruption is separate from an explicit logical cancellation.
Request-owned blocking calls are drained before the owner closes their streams
or releases its slot, including repeated `asyncio.Task.cancel()` calls. The
original control exception still reaches the owner runtime; retries receive an
existing typed error instead of inheriting that exception. Interrupted preparation
is a released `503 provider_unavailable`; after executor/store handoff, the
unknown outcome is retained as `502 provider_execution_failed`. Explicit logical
cancellation takes precedence. Shutdown of the entire process still ends the
process-local guarantee.

## Limits and consumer responsibilities

Every named upload attempt is hashed, including a retry, to verify the bytes
actually supplied. The **hash pass** reads positive chunks of at most 1 MiB,
stops after the existing REST limit plus one byte, and rewinds the request-owned
stream. It adds one sequential pass without another full in-memory copy or
artifact download. If `UploadFile.size` is unknown, the initial size check also
reads the stream separately and may cross the byte limit by up to one 1 MiB chunk.
Hashing does not poll logical cancellation between chunks: it is observed after
that bounded preparation step. Existing HTTP/spooling limits still apply;
joining does not bound incoming HTTP requests, concurrent preparation or the
number of waiters. Terminal entries are count-bounded, not byte/TTL-bounded;
resource-budget follow-up is [#182](https://github.com/A11yDevs/acessilia-toolbox/issues/182).
Request binding is separate from execution cache/provenance keys and does not
change their formats.

Matching is available only while the owning replica retains the entry. Eviction,
restart and independent replicas remain outside this guarantee; there is no
durable distributed idempotency. Callers must retry the immutable original
request, use a new operation id for changed work, and validate response contract,
provenance and applicability before applying results. In particular,
[text normalization](text-normalization.md) consumers still check per-target input
fingerprints and the current document revision.
