# Tools and Providers

## Overview

The Toolbox exposes capabilities, not vendor APIs. The following list is
a roadmap, not a mandatory dependency set.

| Family        | Capability examples                                     | Candidate providers                      |
|---------------|---------------------------------------------------------|------------------------------------------|
| Document AI   | `document.structure.extract`, `document.layout.analyze` | docling-serve, MinerU                    |
| OCR           | `document.ocr`                                          | PaddleOCR, Tesseract, Surya              |
| PDF           | `pdf.inspect`, `pdf.render`, `pdf.extract`              | PyMuPDF, pikepdf                         |
| Tables        | `table.extract`                                         | Docling, MinerU, PaddleOCR               |
| Conversion    | `document.convert`                                      | Pandoc                                   |
| Mathematics   | `math.recognize`, `math.convert`, `math.render`         | pix2tex, LaTeXML, other math services    |
| Accessibility | `accessibility.validate`                                | veraPDF and format-specific validators   |
| Artifacts     | `artifact.store`, `artifact.retrieve`                   | MinIO, S3, filesystem                    |
| Cache         | `cache.get`, `cache.put`                                | Valkey, Redis, filesystem/object storage |
| Metadata      | `artifact.metadata.extract`                             | ExifTool                                 |
| Security      | `artifact.scan`                                         | ClamAV                                   |
| Speech        | `speech.recognize`, `speech.synthesize`                 | sherpa-onnx, whisper.cpp, Piper          |

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
math.recognize : image -> structured math
math.convert   : LaTeX <-> MathML or equivalent
math.render    : structured math -> SVG/PNG/etc.
math.validate  : validate mathematical representation
```

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
