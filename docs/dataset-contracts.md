# Dataset REST response contracts

You can also read this documentation in **Brazilian Portuguese**: [Brazilian Portuguese](dataset-contracts.pt-br.md).

The published `contract/toolbox-contract.json` contains `dataset_routes`:
an explicit binding from an HTTP method/path and exact capability version to
the schema of the **raw REST response**. OpenAPI publishes the same object in
each dataset operation's `x-toolbox-dataset-binding` extension. Resolve its
`output_schema` through the contract's `schema_refs` to obtain the published
file in `schemas/`. Consumers need neither Toolbox Python imports nor the
internal capability manifests.

```json
{
  "method": "GET",
  "path": "/v1/datasets/{dataset_id}/splits/{split}/items",
  "capability": "dataset.list_items",
  "output_schema": "artifact/dataset-item-summary-list@1",
  "capability_version": 1
}
```

The generic object/array OpenAPI response models remain in place to preserve
serialization and validation behavior. The extension and exported route map
provide the normative schema binding, rather than introducing typed response
serialization that could add defaults, remove fields or change nulls. Dataset
routes execute the version in that binding, currently version 1, including
the describe query used to resolve the provider. A future capability version
does not implicitly replace the dedicated route's contract.

## Published responses

Paths below are relative to `/v1/datasets`; `(base)` means that path without
a trailing slash. Schema names omit `artifact/`.
All JSON collections are top-level arrays, including empty collections `[]`.
There is no `document` or pagination envelope around the REST response.

| Method/path | Capability at version 1 | REST response schema |
|---|---|---|
| `GET (base)` | `dataset.list` | `dataset-list@1`: array of `DatasetInfo` |
| `GET /{dataset_id}` | `dataset.describe` | `dataset-info@1`: one `DatasetInfo` |
| `GET /{dataset_id}/splits` | `dataset.list_splits` | `dataset-splits@1`: array of `SplitInfo` |
| `GET /{dataset_id}/splits/{split}/items` | `dataset.list_items` | `dataset-item-summary-list@1`: array of `ItemSummary` |
| `GET /{dataset_id}/splits/{split}/items/{item_id}` | `dataset.get_item` | `dataset-item@1`: one full `DatasetItem` |
| `GET /{dataset_id}/splits/{split}/items/{item_id}/artifacts/{path}` | `dataset.get_artifact` | `binary@1`: raw bytes, not a JSON model |
| `GET /{dataset_id}/splits/{split}/sample` | `dataset.sample` | `dataset-sample@1`: array of full `DatasetItem` |
| `POST /{dataset_id}/sync` | `dataset.sync` | `dataset-sync-report@1`: array of sync events |

`revision` retains its existing optional Git/HuggingFace revision semantics.
Item listings use `limit` (1–1000, default 100) and `offset` (at least 0,
default 0). Sample uses `n` (1–100, default 5) and may return fewer items
than requested. Sync optionally restricts a `split`. Query constraints remain
published in OpenAPI. During item listing, exceptions raised by a provider remain
errors (represented as `Error:` text in MCP); they are not converted into
successful empty lists.
Listings have an existing exception: a missing GitHub manifest or HuggingFace
directory (upstream HTTP 404) returns `[]`, just like a legitimately empty split.
An empty listing alone therefore does not prove that the upstream content exists.
Unknown datasets and missing items still raise errors. This contract publication
preserves those provider behaviors.

### Summaries and full items

`ItemSummary` describes a lightweight listing: `id`, dataset/split/revision,
`artifact_count`, `has_annotations` and open `metadata`. Full items contain
`artifacts` and `annotations` instead. Each reference describes a dataset
`path`, media type and optional content metadata; it is not the separate
content-addressed `artifact/reference@1` contract.

The existing execute manifests of `dataset.list_items@1` and
`dataset.sample@1` both name `artifact/dataset-item-list@1`. That shared family
is published as an array accepting summary or full-item records. Its permissive
full-item branch retains the existing `dataset-item@1` required-field policy;
it is not a discriminator or a substitute for the precise route schema.
Clients generating types for REST must select the route binding above,
not guess the element type from that shared execute reference.

No capability id, version or manifest output reference changes. Publishing
the shared family and precise route schemas requires no incompatible change
to the existing payloads. Future changes that cannot preserve those contracts
require a new version, rather than redefining the version-1 routes silently.

### Sync events

Sync returns an array, not an aggregate object or a count. Examples:

```json
[
  {"status": "mirrored", "item": "one", "split": "input", "artifact": "input/one.pdf"},
  {"status": "skipped", "item": "two", "split": "input", "reason": "item unavailable"},
  {"status": "skipped", "item": "three", "split": "input", "artifact": "input/three.pdf", "reason": "artifact unavailable"}
]
```

When mirroring is disabled the current response is
`[{"status":"skipped","reason":"mirroring is disabled"}]`. A successful sync
with no items returns `[]`. Mirrored events require item, split and artifact;
skipped events require a reason, with item/artifact context when available.
This publishes the existing adapter behavior; it does not change mirroring,
storage, retries or handling of individual failures.

## Schema maintenance and compatibility

The object schemas in `schemas/` remain the source contracts. The collection
exporter embeds their definitions so every generated collection file is
self-contained and uses only local references. `dataset-item@1` embeds the
same `DatasetArtifactRef` definition as `dataset-artifact@1`; a test rejects
divergence between them.

The pre-existing object schemas are widened only to accept nulls that the
providers already serialize: `DatasetInfo.item_count`, `license`, `citation`,
and `DatasetArtifactRef.sha256`/`artifact_id`. Existing required properties,
non-null constraints and extension-field acceptance are preserved. The old
schemas rejected real default responses; the fix adds no producer fields and
changes no serialized value. Dataset path references remain distinct from
artifact-store references.

After editing an object schema or the route bindings, run from the repository root:

```bash
python scripts/export_dataset_schemas.py
python scripts/export_contract.py
UPDATE_OPENAPI=1 pytest tests/integration/test_rest_api.py -k committed_openapi
python scripts/export_dataset_schemas.py --check
python scripts/export_contract.py --check
pytest tests/unit/test_dataset_contracts.py tests/unit/test_schema_files.py
```

Do not edit generated collection schemas, the static contract or OpenAPI by
hand. Drift checks and serialized REST/MCP conformance tests exercise both
GitHub and HuggingFace providers using simulated upstream HTTP. They cover
nullable fields, nested artifact references, pagination, full-item sampling,
empty results, all sync event shapes, binary retrieval and errors. Runtime
enforcement of every provider's output is the separate [#117](https://github.com/A11yDevs/acessilia-toolbox/issues/117)
work; this change supplies the public contracts it can consume.

This resolves the publication gap tracked in [#158](https://github.com/A11yDevs/acessilia-toolbox/issues/158)
for the SDK's [binding-generation work](https://github.com/A11yDevs/acessilia/issues/213).
The SDK capture/generation step must retain `dataset_routes` and refresh the
selected schemas from the same immutable Toolbox commit. Its existing
allowlist, provider and retry decisions remain SDK-owned. The provider/reference
changes in [PR #155](https://github.com/A11yDevs/acessilia-toolbox/pull/155) must
preserve these wire examples or update their contracts explicitly; after any
schema change, regenerate the collections and run the same conformance suite.
