# DOCX export

> A Brazilian Portuguese translation is available in [docx-export.pt-br.md](docx-export.pt-br.md).

``` text
document.export.docx : Pandoc JSON AST -> Word document (stored artifact)
```

The `pandoc-docx` provider calls the `/render/docx` route of the same sidecar
that serves [PDF/UA export](pdf-ua-export.md) (`pdf-ua-render`, built from
`docker/pdf-ua-render.Dockerfile`). It reuses that image's pinned Pandoc and
needs no TeX. Configure the endpoint with `PDF_UA_RENDER_URL`.

Pandoc keeps the document structure that assistive technology relies on:
headings become Word heading styles, tables keep their header row, and math
becomes Office Math, which screen readers can read.

## Request

Send the AST as `application/json`:

``` bash
curl -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F file=@document.ast.json\;type=application/json \
  -F 'parameters={"lang":"pt-BR"}' \
  http://localhost:8002/v1/capabilities/document.export.docx:execute
```

| Parameter | Default | Meaning |
|---|---|---|
| `lang` | `pt-BR` | BCP 47 tag written as the document language (`w:lang`) |

The renderer reads nothing but the AST:

- Raw OpenXML nodes are rejected anywhere in the AST: the sidecar answers
  `422`, which the API reports as `400`. Raw OpenXML would otherwise be pasted
  into the document unchecked. Every other raw format (TeX, HTML, Markdown, ...)
  is dropped by Pandoc without notice, and the request still succeeds.
- Pandoc runs with `--sandbox`, so an image or include that points to a local
  file or URL is left out instead of read. A dropped image is replaced by its
  alt text, which keeps the document readable; images are expected to be
  already reduced to text alternatives.

## Response

The DOCX is never inlined. The executor stores it in the artifact store and the
result carries a reference (schema `artifact/docx-export@1`):

``` json
{
  "document": {
    "artifact_id": "sha256:…",
    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "size_bytes": 10521,
    "toolchain": {"version": "1.1.0", "pandoc": "3.9.0.2"}
  },
  "artifacts": [
    {"media_type": "application/json"},
    {"media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
  ]
}
```

Download it with `GET /v1/artifacts/{artifact_id}`.

## Cache

The capability is cacheable. The key covers the AST, `lang`, the sidecar
version and the Pandoc version. TeX Live and the PDF/UA template do not shape a
DOCX, so upgrading them keeps the cached DOCX files. As with PDF/UA, a cache hit
is served only while the stored file still exists, so the capability requires an
artifact store.

The sidecar has a single version number for both routes, so any sidecar
release, even one that only fixes the PDF path, invalidates the cached DOCX
files too.

## Build

The sidecar build also converts a smoke-test AST to DOCX, so a Pandoc that
cannot write DOCX fails the build. See [PDF/UA export](pdf-ua-export.md#build).

## Availability

The sidecar's `/health` checks Pandoc, the TeX engine and the PDF/UA template,
although `/render/docx` needs only Pandoc. A broken TeX install therefore marks
the whole sidecar unhealthy and takes `document.export.docx` down with it. This
is the accepted cost of sharing one image; split the DOCX route into its own
sidecar if its availability must not depend on TeX Live.
