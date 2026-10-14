# Tools and Providers

## Overview

The Toolbox exposes capabilities, not vendor APIs. Providers marked
**[implemented]** are wired in `providers-config.yaml`; the rest remain
roadmap candidates.

| Family        | Capability examples                                     | Providers                                  | Status        |
|---------------|---------------------------------------------------------|--------------------------------------------|---------------|
| Document AI   | `document.structure.extract`, `document.layout.analyze` | docling-serve, MinerU                      | [implemented] |
| OCR           | `document.ocr`                                          | docling-serve, MinerU (PaddleOCR, Surya: roadmap) | [implemented] |
| PDF           | `pdf.split`, `pdf.render`                               | PyMuPDF                                    | [implemented] |
| Tables        | table extraction inside `document.structure.extract`    | Docling (TableFormer), MinerU              | [implemented] |
| Conversion    | `document.convert`                                      | Pandoc                                     | roadmap       |
| Mathematics   | `math.recognize`, `math.convert`, `math.verbalize`      | docling-serve, pure-python (latex2mathml)  | [implemented] |
| Media triage  | `media.inspect`                                         | pure-python (Pillow, in-process, optional extra) | [implemented] |
| Music         | `music.omr`                                             | homr (in-process), Audiveris (sidecar)     | [implemented] |
| Page layout   | `image.page.orient`                                     | RapidOCR (sidecar)                         | [implemented] |
| Chemistry     | `chem.recognize`, `chem.convert`                        | docling-serve VLM (Granite Vision), pure-python mhchem | [implemented] |
| Accessibility | `accessibility.validate`                                | pure-python checks (veraPDF: roadmap)      | [implemented] |
| Artifacts     | `artifact.store`, `artifact.retrieve`                   | MinIO, S3                                  | [implemented] |
| Cache         | `cache.get`, `cache.put`                                | Valkey, Redis                              | [implemented] |
| Metadata      | `artifact.metadata.extract`                             | ExifTool                                   | roadmap       |
| Security      | `artifact.scan`                                         | ClamAV                                     | roadmap       |
| Speech        | `speech.recognize`, `speech.synthesize`                 | sherpa-onnx, whisper.cpp, Piper            | roadmap       |

## Docling / docling-serve

The original Structure Extractor established a useful pattern: run
Docling externally through docling-serve to isolate PyTorch/model
dependencies and allow independent scaling. The Toolbox should retain
this approach, but Docling becomes one provider of normalized
capabilities.

Potential capabilities:

``` text
document.structure.extract
document.layout.analyze
table.extract
```

## MinerU

MinerU can provide overlapping document-understanding capabilities.
Overlap is useful for experimentation, fallback, and explicit agent
policy, but providers are interchangeable only when they satisfy the
same capability contract.

## OCR

OCR should be independently addressable rather than hidden only inside
document-understanding providers. This makes scanned-document pipelines
composable and enables controlled comparisons.

## Pandoc

Pandoc is a strong candidate for deterministic document conversion. It
should be treated as a renderer/converter rather than the canonical
internal document representation.

## Mathematics

Separate concerns:

``` text
math.recognize : image -> structured math (docling-serve)
math.convert   : LaTeX <-> MathML (pure-python, latex2mathml)
math.verbalize : LaTeX -> natural language (pure-python, pt-BR)
```

These capabilities accept `artifact/latex@1`, a JSON object carrying the
expression under `latex`:

``` json
{ "latex": "x^2+1" }
```

`text/plain` is accepted too, and then the body is the expression itself. The
JSON form is the one to prefer: it is the declared schema and it leaves room for
`confidence`, `page` and `bbox` when the expression came from recognition.

Note: UniMERNet — the formula recognition model used internally by
MinerU's pipeline backend — is reached through `document.structure.extract`
and `math.recognize` (docling-math); the Toolbox does not run it as a
standalone service.

## Tables

Table extraction is exposed through `document.structure.extract` with
Docling's TableFormer model. The `table_mode` option (`fast` |
`accurate`) can be set in the provider's `config` (providers-config.yaml)
or overridden per request via the `table_mode` parameter. Setting
`pipeline: vlm` (optionally with `vlm_engine`) switches docling-serve to
the Granite Vision backend.

## Music (optical music recognition)

``` text
music.omr : sheet-music image/PDF -> MusicXML or MEI
```

Two interchangeable providers:

- `audiveris` — HTTP adapter for the `audiveris-serve` sidecar container
  (Java Audiveris + Tesseract behind a small REST wrapper). Configure via
  `AUDIVERIS_SERVE_URL`. **This is the only provider usable in the published
  image**: build the sidecar with
  `docker build -f docker/audiveris-serve.Dockerfile -t audiveris-serve:latest .`
  and start it with `docker compose up -d audiveris-serve`.
- `homr` — runs the homr Python package in-process (install the
  `music` extra: `pip install .[music]`). Only viable for a local, derived
  install: `homr` depends on `rapidocr`, which the Delivery guardrail forbids in
  the published image, so the `music` extra can never be part of it.

Audiveris is a desktop application: the sidecar runs it in batch mode under
Xvfb, and it exports compressed MusicXML (`.mxl`), which the sidecar unpacks
before returning the score.

## Page orientation

``` text
image.page.orient : raster page image -> detected angle, optionally upright image
```

A single provider, `rapidocr-orient`, runs RapidOCR with ONNX Runtime in a
sidecar container (`docker/rapidocr-orient.Dockerfile`). Configure the endpoint
with `RAPIDOCR_ORIENT_URL`.

Detection combines the OCR angle classifier — which resolves the 180° case that
horizontal geometry cannot reveal — with a comparison of how well the page reads
at 0°, 90° and 270°.

Set `parameters={"auto_rotate": true}` when extracting structure from an image to
run this as opt-in preprocessing. The decision is recorded in
`provenance.orientation`; if the sidecar is unavailable the extraction continues
unrotated. See [rapidocr-orient.md](rapidocr-orient.md).

## Chemistry

``` text
chem.recognize : image -> reaction description + mhchem (docling-serve VLM)
chem.convert   : \ce{...} LaTeX -> structured reaction + pt-BR text (pure-python)
```

`chem.convert` takes the same `artifact/latex@1` payload as the math
capabilities, so the expression travels under `latex`:

``` json
{ "latex": "\\ce{CH4 + 2O2 -> CO2 + 2H2O}" }
```

`chem.recognize` uses docling-serve's VLM pipeline (Granite Vision) and
extracts `\ce{}` expressions from the model output. `chem.convert`
normalizes mhchem syntax (coefficients, charges, states of matter,
reaction arrows with conditions) into a structured form matching
`artifact/mhchem@1`, including a human-readable pt-BR rendering for
verbalization.

## Accessibility validators

Validation is valuable as deterministic feedback to agentic reflection
loops. A validator reports facts; the Agentic Core decides whether to
repair, retry, escalate, or request human review.

## Provider lifecycle

Starting/stopping heavy services may be exposed as an explicit
infrastructure capability or deployment concern. The Toolbox must not
autonomously unload a service when doing so would constitute an
application-level decision. Technical orchestration policies must be
explicit and observable.
