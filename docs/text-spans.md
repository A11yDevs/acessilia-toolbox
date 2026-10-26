# Text spans (`document.text.spans`)

> A Brazilian Portuguese translation is available in [text-spans.pt-br.md](text-spans.pt-br.md).

`document.text.spans` reads the text layer of a PDF as lines of spans. Each span carries its font, size, style flags and geometry. The provider is `pymupdf-pdf` (PyMuPDF, in-process). The capability is deterministic and cacheable, and it runs no model.

It exists for what layout models do not report. In a typeset PDF, inline math is visible only in its fonts and baselines: TeX math fonts (`CMMI`, `CMSY`, `CMEX`, `MSBM`), and smaller spans that sit above or below the baseline for scripts. The Acessilia core uses it to find inline math in paragraphs (A11yDevs/acessilia#265, ADR 0011 there).

## Parameters

| Name | Type | Default | Meaning |
|---|---|---|---|
| `pages` | list of 1-based integers | every page | Pages to read. Duplicates are dropped and the pages come back in order. A page out of range is refused. |

A call reads at most 1000 pages. A longer document must name its pages. At about 45 KiB of JSON per page of dense text, requesting pages in batches also keeps each response small.

## Result (`artifact/text-spans@1`)

The `s₀` of `s₀ ∈ ℝ` on page 1 of the Acessilia math reference case (other spans omitted):

```json
{
  "page_count": 5,
  "coord_origin": "TOPLEFT",
  "pages": [
    {
      "page_number": 1, "width": 612.0, "height": 792.0, "rotation": 0,
      "lines": [
        {
          "bbox": [64.92, 214.0, 532.67, 230.75],
          "spans": [
            {"text": "s", "font": "CMMI12", "size": 11.96, "flags": 4,
             "bbox": [498.96, 216.99, 504.47, 228.95], "origin": [498.96, 225.96]},
            {"text": "0", "font": "CMR8", "size": 7.97, "flags": 4,
             "bbox": [504.48, 221.78, 508.71, 229.75], "origin": [504.48, 227.76]}
          ]
        }
      ]
    }
  ]
}
```

- **Coordinates.** They are PDF points in the unrotated page space, with the origin at the top-left corner and `y` growing downwards. A rotated page reports `rotation` and keeps its unrotated `width` and `height`.
- **`origin`.** The start of the span's baseline. A subscript has a larger `y` than the text around it, and a superscript a smaller one.
- **`flags`.** PyMuPDF's style bits: 1 superscript, 2 italic, 4 serif, 8 monospaced, 16 bold.
- **`font`.** The font name without the subset prefix (`ABCDEF+CMMI12` becomes `CMMI12`).
- **Whitespace.** Spans made only of whitespace are dropped, and the other spans are trimmed. TeX writes inter-word glue as spans of its own, and PyMuPDF adds spaces where it sees a gap. One such space at the start of a subscript would otherwise report the body's baseline. The gaps are still visible in the boxes.
- **Image blocks** are skipped.
