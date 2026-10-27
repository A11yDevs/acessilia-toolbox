# PDF/UA export

``` text
document.export.pdf_ua : Pandoc JSON AST -> tagged PDF/UA-1 (stored artifact)
```

The `pandoc-pdf-ua` provider calls the `pdf-ua-render` sidecar
(`docker/pdf-ua-render.Dockerfile`), which owns the whole toolchain: Pandoc, a
TeX Live release installed from that year's frozen `tlnet-final` repository,
DejaVu fonts and the `pdf_ua.tex` template. Configure the endpoint with
`PDF_UA_RENDER_URL`. To check the result against PDF/UA, see
[PDF/UA validation](pdf-ua-validation.md).

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
curl -H "Authorization: Bearer $TOOLBOX_API_KEY" \
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
by Pandoc, except the formula alternate text below, which the sidecar's own
filter writes after replacing every TeX special character with a space. Shell escape
is off and TeX may only write below its working directory.

### Formula alternate text

Each formula becomes a `Formula` structure element whose `/Alt` is what a
screen reader announces. By default `latex-lab` fills it with the LaTeX source
(`LaTeX formula starts \begin {math} \frac {…} … LaTeX formula ends`). To give
a formula its spoken form, wrap the `Math` in a span with the class
`accessilia-formula` and the text in an `alt` attribute:

``` json
{"t": "Span", "c": [["", ["accessilia-formula"], [["alt", "a fração com numerador s de 4 menos s de 2 …"]]],
                    [{"t": "Math", "c": [{"t": "DisplayMath"}, "\\frac{s(4)-s(2)}{4-2}"]}]]}
```

`filters/formula_alt.lua` turns the span into a TeX group that calls
`\AccessiliaFormulaAlt{…}` (defined in `pdf_ua.tex`) before the formula, so
only that formula's `/Alt` changes. The filter first replaces each of
`\ { } $ & # ^ _ % ~` in the text with a space and collapses the spaces
(`a}b` reads `a b`), so the text can neither inject commands nor unbalance the
group. A span without the class, or with an empty `alt`, renders its content
as before, and other writers (`document.export.docx`) ignore the span.

### Math font

Formulas are set with `unicode-math` in TeX Gyre DejaVu Math, the OpenType
math font that matches the DejaVu Sans text. Each symbol is written with its
own code point, so the text layer (copy and paste, search, a reader that falls
back from `/Alt`) reads `≠` for `\neq`, not `=` followed by a detached U+0338.
`amsmath` provides the environments (`aligned`); `amssymb` is not loaded,
since it conflicts with `unicode-math`.

Recognizers write the `amssymb` names, so the template keeps all of them
working. `unicode-math` defines most, and `pdf_ua.tex` maps the rest
(`\lozenge`, `\varpropto`, `\nleqslant`, …) to their Unicode-named
equivalents: one undefined command fails the render. The symbols TeX Gyre DejaVu
Math has no glyph for (`\bigstar`, `\subseteqq`, `\pitchfork`, …) fall back to
STIX Two Math instead of rendering as nothing.

### Heading numbers

Headings render unnumbered: the rebuilt document already carries its own
numbering in the heading text, so `pdf_ua.tex` sets `secnumdepth` to
`-\maxdimen` as Pandoc's default template does. A document that wants LaTeX
to number its sections can opt in through the AST metadata, with
`numbersections` set to `true` and, optionally, `secnumdepth` (default `5`):

``` json
{"meta": {"numbersections": {"t": "MetaBool", "c": true}}, …}
```

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
sidecar reports on `/version`: Pandoc, engine, LaTeX kernel, the template
digest and the formula alt filter digest. Rendering the same document again returns the stored PDF without running
LaTeX; upgrading any component produces a new key. Valkey holds only the small
JSON result, never the PDF.

A cache hit is served only while the stored PDF still exists. If the object was
removed from MinIO, the Toolbox renders again instead of returning an id that
cannot be downloaded. This capability therefore requires an artifact store.

## Build

CI publishes the image as `ghcr.io/a11ydevs/acessilia-toolbox-pdf-ua-render`
(see [installation](installation.md#sidecar-images)). To build it locally:

``` bash
docker build -f docker/pdf-ua-render.Dockerfile -t pdf-ua-render:latest .
```

The build renders a smoke-test PDF, so a broken toolchain fails the build rather
than the first request. Override `TEXLIVE_YEAR` or `PANDOC_VERSION` with
`--build-arg` to upgrade deliberately. `PDF_UA_ENGINE` selects `lualatex`
(default, the engine the LaTeX team recommends for tagging) or `xelatex`.
