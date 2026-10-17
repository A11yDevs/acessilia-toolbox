# PDF/UA export

``` text
document.export.pdf_ua : Pandoc JSON AST -> tagged PDF/UA-1 (stored artifact)
```

The `pandoc-pdf-ua` provider calls the `pdf-ua-render` sidecar
(`docker/pdf-ua-render.Dockerfile`), which owns the whole toolchain: Pandoc, a
TeX Live release installed from that year's frozen `tlnet-final` repository,
DejaVu fonts and the `pdf_ua.tex` template. Configure the endpoint with
`PDF_UA_RENDER_URL`.

## Why a sidecar

PDF/UA depends on a fragile combination of the LaTeX kernel, `tagpdf`,
`latex-lab`, `longtable` and Pandoc. A TeX Live updated piecemeal (for example
`tagpdf` newer than the kernel) fails with errors such as
`Undefined control sequence \IfPDFManagementActiveF`. Pinning one image keeps
every consumer on the same, tested toolchain, and keeps several gigabytes of TeX
out of both the Toolbox image and its consumers.

## Request

Send the AST as `application/json`. Images are expected to be already reduced
to text alternatives; the renderer reads nothing but the AST.

``` bash
curl -H "X-API-Key: $TOOLBOX_API_KEY" \
  -F file=@document.ast.json\;type=application/json \
  -F 'parameters={"lang":"pt-BR"}' \
  http://localhost:8002/v1/capabilities/document.export.pdf_ua:execute
```

| Parameter | Default | Meaning |
|---|---|---|
| `lang` | `pt-BR` | BCP 47 tag written to the PDF document metadata |

Raw TeX nodes (`RawBlock`/`RawInline` in `latex`, `tex` or `context`) are
rejected anywhere in the AST, metadata included: the sidecar answers `422`,
which the API reports as `400`. Every string therefore reaches LaTeX escaped
by Pandoc. Shell escape
is off and TeX may only write below its working directory.

## Response

The PDF is never inlined. The executor stores it in the artifact store and the
result carries a reference (schema `artifact/pdf-export@1`):

``` json
{
  "document": {
    "artifact_id": "sha256:…",
    "media_type": "application/pdf",
    "size_bytes": 101575,
    "pdf_standard": "ua-1",
    "engine": "lualatex",
    "toolchain": {"pandoc": "3.9.0.2", "latex_kernel": "2025-11-01 pl0", "template_sha256": "…"}
  },
  "artifacts": [{"media_type": "application/json"}, {"media_type": "application/pdf"}]
}
```

Download it with `GET /v1/artifacts/{artifact_id}`. The id is the SHA-256 of the
bytes, so a consumer that already holds that id can skip the download.

## Cache

The capability is cacheable. The key covers the AST, `lang` and everything the
sidecar reports on `/version`: Pandoc, engine, LaTeX kernel and the template
digest. Rendering the same document again returns the stored PDF without running
LaTeX; upgrading any component produces a new key. Valkey holds only the small
JSON result, never the PDF.

A cache hit is served only while the stored PDF still exists. If the object was
removed from MinIO, the Toolbox renders again instead of returning an id that
cannot be downloaded. This capability therefore requires an artifact store.

## Build

``` bash
docker build -f docker/pdf-ua-render.Dockerfile -t pdf-ua-render:latest .
```

The build renders a smoke-test PDF, so a broken toolchain fails the build rather
than the first request. Override `TEXLIVE_YEAR` or `PANDOC_VERSION` with
`--build-arg` to upgrade deliberately. `PDF_UA_ENGINE` selects `lualatex`
(default, the engine the LaTeX team recommends for tagging) or `xelatex`.
