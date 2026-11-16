# Layout-analysis result contract

Translation: [Brazilian Portuguese](layout-contract.pt-br.md).

`document.layout.analyze@1` publishes `artifact/layout-analysis@1` in
[`schemas/layout-analysis@1.json`](../schemas/layout-analysis@1.json). This
JSON Schema describes the execution response's **`document`**, not the REST
envelope, provenance or artifact references. MCP serializes that document as
JSON in its text content.

## Existing version-1 variants

Version 1 has two existing wire representations. Publishing their union
preserves both; it does not make their fields interchangeable:

| Representation | Current provider | Required document fields |
|---|---|---|
| Compact layout (`LayoutPages`) | `docling-layout` | `pages`, `page_count`, `region_count` |
| Processing manifest (`MineruLayoutResult`) | `mineru-layout` | Existing `processing-manifest:1.1.0` fields, plus layout metadata in `extractor.configuration` |

Both variants serialize `pages` as an **array**. Docling's upstream page map
or list is converted to that array; the upstream map is not a third result
variant. The SDK's current map-tolerant reader does not define a public map
contract. Consumers must identify the representation before reading regions.
In particular, the existing SDK `analyze_pages` helper alone does not extract
MinerU's classification metadata from the processing manifest.

A future common representation that replaces either payload needs a new
capability/schema version and explicit client migration. This publication
changes no provider, execution, transport, retry or SDK selection behavior.

## Compact layout

An example of the current Docling result:

```json
{
  "pages": [{
    "page_number": 2,
    "width": 595,
    "height": 842,
    "regions": [{
      "type": "table",
      "label": "table",
      "bbox": [50, 342, 500, 642],
      "confidence": 0.95,
      "text": "Table",
      "page_number": 2
    }]
  }],
  "page_count": 1,
  "region_count": 1
}
```

Page numbers are one-based source page numbers, not array offsets. Width and
height are page points; `0` represents an unknown dimension. A region's four
coordinates are `[left, top, right, bottom]` in page points, with a top-left
origin and y growing downwards. Bottom-left boxes are flipped using page
height; unresolved origins or missing height for that conversion omit the
region instead of claiming another coordinate convention.

`type` is one of `text_clean`, `text_scanned`, `embedded_image`, `table`,
`formula`, `code_block`, `list_block`, `callout_box`, `unknown` or `ignore`.
The adapter omits regions classified as `ignore`. `label` retains the source
label. `confidence` is a probability or null; an absent upstream confidence
currently becomes `1.0`. `text` may be null. All illustrated fields are
required; compact documents, pages and regions accept extension fields.

Only pages with accepted regions are emitted. With none, the result is
`{"pages":[],"page_count":0,"region_count":0}`, even when upstream page
geometry exists. Counts refer to emitted pages and regions, not necessarily
all pages of the source file.

## Processing manifest and MinerU classifications

The second branch embeds the existing
[`structured-document@1.json`](../schemas/structured-document@1.json)
contract. Its canonical document/geometry fields remain owned by docstruct
and checked by the existing semantic contract tests. No new canonical page,
element or box model is introduced here.

The following is a **fragment** of `extractor.configuration`, not a complete
layout document:

```json
{
  "backend": "mineru-layout",
  "region_count": 1,
  "layout_regions": [{
    "type": "text",
    "source_label": "text",
    "raw_label": "text",
    "page": 0,
    "bbox": {"left": 50, "top": 100, "right": 500, "bottom": 200},
    "text_preview": "Text"
  }]
}
```

All three configuration fields and all illustrated region fields are
required. Classification `type` is `text`, `title`, `list_block`, `table`,
`embedded_image`, `formula`, `ignore` or `unknown`. `source_label` is the
adapter's normalized source label; `raw_label` retains the upstream label.
`text_preview` is at most the current 120-character preview or null. There
is no confidence field; consumers must not invent one.

Metadata `page` is zero-based; canonical pages and element provenance use
one-based `page_number`. The metadata box has no origin field. When source
page geometry exists, MinerU's normalized coordinates are converted to page
points. Without it, coordinates can remain normalized; consumers must use
canonical element provenance and its `coord_origin` (`TOPLEFT`, `BOTTOMLEFT`
or `UNKNOWN`) before interpreting geometry. An unknown origin is not a
promise of page points.

A successful result with no blocks retains available canonical pages and
has empty `elements` and `layout_regions`, and zero classification count.
The canonical manifest keeps its existing required/optional/null rules and
extension policy; layout configuration and classification records accept
extensions. Provider exceptions remain REST errors or MCP `Error:` text,
rather than fabricated empty success documents.

## Maintenance and validation

Regenerate with `python scripts/export_layout_schema.py` and
`python scripts/export_contract.py`. Both support `--check`. The layout
schema uses Draft 2020-12 and local references only. Its generator embeds
the canonical schema's definitions and constraints directly; only root
identifier/dialect/description metadata is replaced. Tests reject drift in
both the generated file and the embedded canonical definitions.

The JSON Schema validates shapes and scalar constraints. Count equality,
page/region correspondence and box ordering are not cross-field JSON Schema
assertions; representative provider tests check those emitted values.
Conformance tests invoke real providers and the executor through REST and
MCP, simulating only upstream HTTP. They cover Docling page maps/lists,
MinerU 4's published MiddleJson fixture, empty results, geometry and failures.
This publication does not add runtime output enforcement for arbitrary
provider responses; that remains [#117](https://github.com/A11yDevs/acessilia-toolbox/issues/117).

This resolves the contract prerequisite in
[#159](https://github.com/A11yDevs/acessilia-toolbox/issues/159). Downstream
[SDK binding work](https://github.com/A11yDevs/acessilia/issues/213) must
capture this schema and contract from the same immutable Toolbox SHA,
handle the variants explicitly, and reuse docstruct for the canonical branch.
