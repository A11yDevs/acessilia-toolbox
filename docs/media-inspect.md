# Media Inspect Capability (`media.inspect`)

The `media.inspect` capability provides deterministic, in-process triage of
document and image artifacts before heavy structural extraction or OCR
pipelines are invoked.

## Overview

When processing raw artifacts (standalone diagrams, maps, scanned pages), heavy
document extraction engines such as Docling or MinerU may misclassify pure
images as sparse documents, for example by finding a few text characters while
failing to detect any picture bounding box.

`media.inspect` answers that question without ML runtimes or GPU infrastructure.
It reads the actual container bytes, measures real dimensions, and reports the
page count, MIME type, and whether visual inspection is required.

## Provider: `pure-media-inspect`

- **Transport:** in-process
- **Engine:** Python standard library (`mimetypes`, `io`) plus Pillow, with
  PyMuPDF used when installed for exact PDF page metrics
- **Execution:** deterministic, cacheable, sub-millisecond overhead for images

Pillow is an optional dependency. Install it with the `media` extra:

```bash
pip install "acessilia-toolbox[media]"
```

Without Pillow the provider reports itself unhealthy instead of guessing.
Without PyMuPDF, PDF page counts fall back to scanning the content stream and
`dimensions` is reported as `null` rather than as an assumed page size.

## Supported Input Media Types

| Declared media type | Detection |
|---|---|
| `image/png` | Pillow |
| `image/jpeg` | Pillow |
| `image/webp` | Pillow |
| `image/tiff` | Pillow |
| `image/bmp` | Pillow |
| `application/pdf` | PyMuPDF, or content-stream scan |

The MIME type is derived from the container, not from the file name or the
request's declared media type. A JPEG named `foto.png` is reported as
`image/jpeg` with `declared_media_type: image/png`.

## Output Schema

The output complies with `schemas/media-inspection@1.json`:

```json
{
  "media_type": "image/jpeg",
  "declared_media_type": "image/jpeg",
  "file_size_bytes": 33267,
  "is_image": true,
  "is_pdf": false,
  "page_count": 1,
  "page_count_source": "pillow",
  "dimensions": {
    "width": 1920,
    "height": 1080,
    "aspect_ratio": 1.7778
  },
  "dimensions_source": "pillow",
  "color_mode": "RGB",
  "format": "JPEG",
  "artifact_nature": "standalone_image",
  "document_genre": null,
  "genre_signals": [],
  "requires_visual_inspection": true,
  "metadata": {
    "filename": "diagram.jpg"
  }
}
```

`dimensions`, `dimensions_source`, `color_mode`, and `format` are `null` when
the artifact cannot be decoded. `page_count_source` is `pillow`, `pymupdf`,
`content_stream_scan`, or `null` when the page count could not be verified.

## Classification Heuristics (`artifact_nature`)

- `standalone_image`: an image outside the portrait document aspect band, such
  as an infographic, diagram, map, or landscape photo. Sets
  `requires_visual_inspection: true`.
- `scanned_page`: an image matching the portrait document aspect band (0.65 to
  0.78, roughly A4/US Letter) whose shorter side is at least
  `min_dimension_px` pixels. Sets `requires_visual_inspection: false`.
- `paginated_document`: a PDF, or a multi-frame image container.
- `unknown`: unrecognized or unsupported byte stream.

## Document Genre (`document_genre`)

For a PDF, `document_genre` tells a slide deck (`presentation`) from any other
document (`document`), so a consumer can structure a deck slide by slide.
`genre_signals` says why; either signal is enough:

- `producer:<name>` / `creator:<name>`: the PDF metadata names, as a whole
  word, software that only makes decks. `<name>` is a lowercase token, one of
  `powerpoint`, `keynote`, `impress` or `beamer`, whatever the casing in the
  metadata. Google Slides is not listed: its PDFs share the "Google" producer
  with Google Docs.
- `geometry:landscape-uniform`: two or more pages, at least 90% of them with
  the same landscape size, with a width/height ratio within 0.02 of 4:3, 16:10
  or 16:9. Landscape paper (Letter 1.29, A4 1.41, Legal 1.65) falls outside, so
  a landscape report stays a `document`.

The genre is best-effort: when a later page or the metadata cannot be read,
`document_genre` is `null` and the rest of the inspection is still reported.

`document_genre` is `null` for images and for PDFs inspected without PyMuPDF,
whose pages are not measured. Providers before `pure-media-inspect` 1.1.0 do
not report the field, so consumers must treat its absence as unknown.

`min_dimension_px` and `scanned_page_aspect_ratio` can be supplied as request
parameters to override the defaults (`200` pixels, `[0.65, 0.78]`). Passed
parameters participate in cache fingerprinting.

## Usage Example

```bash
curl -X POST "$TOOLBOX_URL/v1/capabilities/media.inspect:execute" \
  -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F 'file=@diagram.png;type=image/png'
```

Override the scanned-page thresholds:

```bash
curl -X POST "$TOOLBOX_URL/v1/capabilities/media.inspect:execute" \
  -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F 'file=@page.png;type=image/png' \
  -F 'parameters={"min_dimension_px": 400}'
```

See also the [constitution](constitution.md) for the principles that keep this
capability provider-neutral.
