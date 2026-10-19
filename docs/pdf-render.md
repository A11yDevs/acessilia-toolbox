# Page rendering (`pdf.render`)

> A Brazilian Portuguese translation is available in [pdf-render.pt-br.md](pdf-render.pt-br.md).

`pdf.render` turns one page of a document into a PNG image. The provider is
`pymupdf-pdf` (PyMuPDF, in-process). Two versions of the contract are
published side by side.

| | Version 1 | Version 2 (latest) |
|---|---|---|
| Inputs | PDF | PDF, PNG, JPEG |
| Parameters | `page_number`, `dpi` | `page_number`, `dpi`, `max_side`, `clip` |
| Result | `artifact/image@1`, PNG inline as base64 | `artifact/image@2`, PNG stored as an artifact (`by_reference`) |
| Selection | explicit `version=1` only | default when no version is given |

Version 1 stays executable through REST (`version=1`) and the CLI
(`--capability-version 1`) while clients migrate. MCP tools, which take no
version, and PDDL actions expose only version 2. The catalogue flags version 2
with `latest: true` (see [api.md](api.md#capability-versions)).

## Version 2

### Parameters

| Name | Type | Default | Meaning |
|---|---|---|---|
| `page_number` | integer ≥ 1 | `1` | 1-based page. A PNG or JPEG input has exactly one page. |
| `dpi` | integer 1–600 | `150` | Resolution: the scale is `dpi / 72` pixels per page unit. |
| `max_side` | integer 16–10000 | none | Bound, in pixels, of the longest side of the output. When given with `dpi`, the smaller scale wins. |
| `clip` | object | whole page | Region to render: `left`, `top`, `right`, `bottom` in page units, plus `origin` (`TOPLEFT`, the default, or `BOTTOMLEFT`). |

**Page units** are PDF points. For raster inputs they are the points PyMuPDF
derives from the image resolution: pixels at 72 dpi when the image declares
none. The response reports the page size in the same units.

**Rotated pages:** for a PDF page with `/Rotate`, page units are those of the
page **as rotated**, the way it is displayed. The `clip` (including the
`BOTTOMLEFT` conversion), the reported `page_width`/`page_height` and the PNG
all follow that space: a 200 × 100 page with `/Rotate 90` is 100 × 200.
Coordinates taken from the unrotated space (the `MediaBox`) clip the wrong
region.

**Clip semantics:**
- A `BOTTOMLEFT` box is converted to a top-left origin with the page height.
- Swapped corners are normalized.
- The box is intersected with the page.
- A box that does not intersect the page, or that leaves less than one unit
  in either direction, is refused.
- Unknown fields, non-numeric coordinates (numeric strings and booleans
  included), non-finite coordinates and other origins are refused.

**Limit:** a render above 20,000,000 pixels (width × height) is refused;
lower `dpi` or set `max_side`.

### Result (`artifact/image@2`)

The PNG is stored in the artifact store and fetched with
`GET /v1/artifacts/{artifact_id}`. The response `artifacts` list carries its
reference next to the JSON document. The document reports what was actually
applied:

| Field | Meaning |
|---|---|
| `artifact_id`, `media_type`, `size_bytes` | The stored PNG (`image/png`) |
| `page_number`, `page_count` | Page rendered and pages in the source |
| `page_width`, `page_height` | Page size in page units |
| `clip` | Region rendered, top-left origin, already intersected with the page (the whole page without a clip) |
| `scale`, `dpi` | Pixels per page unit, and `scale × 72` |
| `width_px`, `height_px` | PNG size in pixels |
| `source_media_type`, `source_filename` | The input |

Rendering is deterministic, idempotent and cacheable: the cache key covers the
capability version, the input fingerprint and the parameters.

## Version 1

Kept unchanged for compatibility: PDF input only, `page_number` and `dpi`, and
the PNG inline as `image_bytes_base64` (`artifact/image@1`). Request it with
`version=1`.
