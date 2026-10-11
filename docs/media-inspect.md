# Media Inspect Capability (media.inspect)

The media.inspect capability provides deterministic, in-process triage and inspection for document and image artifacts before heavy structural extraction or OCR pipelines are invoked.

## Overview

When processing raw artifacts (e.g. standalone diagrams, maps, or scanned pages), heavy document extraction engines like Docling or MinerU may misclassify pure images as sparse documents (for example, finding only a few text characters while failing to detect any picture bounding box).

The media.inspect capability solves this by evaluating format headers, dimensions, aspect ratio, page count, and MIME types in pure Python without requiring any ML runtimes or GPU infrastructure.

## Provider: pure-media-inspect

- **Transport:** in_process
- **Engine:** Python standard library (mimetypes, io) + Pillow (PIL.Image) + PyMuPDF (optional, for PDF page metrics)
- **Execution:** Deterministic, cacheable, sub-millisecond overhead.

### Supported Input Media Types

- image/png
- image/jpeg
- image/webp
- image/tiff
- image/bmp
- pplication/pdf

## Output Schema (rtifact/media-inspection@1)

The output complies with schemas/media-inspection@1.json:

`json
{
  media_type: image/jpeg,
  file_size_bytes: 1048576,
  is_image: true,
  is_pdf: false,
  page_count: 1,
  dimensions: {
    width: 1920,
    height: 1080,
    aspect_ratio: 1.7778
  },
  color_mode: RGB,
  format: JPEG,
  artifact_nature: standalone_image,
  requires_visual_inspection: true,
  metadata: {
    filename: planisferio_politico.jpg
  }
}
`

### Classification Heuristics (rtifact_nature)

- standalone_image: Images that do not match standard paginated document aspect ratios (e.g. infographics, diagrams, maps, landscapes). Automatically flags equires_visual_inspection = true.
- scanned_page: Single images matching portrait document dimensions (~A4/US Letter aspect ratio around 0.65–0.78).
- paginated_document: Multi-page or single-page PDF documents.
- unknown: Unrecognized or unsupported binary stream.

## Usage Example

`ash
curl -X POST http://localhost:8002/v1/capabilities/media.inspect:execute \
  -H Authorization: Bearer  \
  -F 'file=@diagram.png;type=image/png'
`
