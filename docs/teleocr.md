# Optional TeleOCR provider

For measured gains, failure cases and pending validation, see the
[local evaluation report](teleocr-evaluation.md).

TeleOCR is an explicitly selected alternative for `document.structure.extract`
on JPEG/PNG pages. The Toolbox adapter does not load weights, import PyTorch,
choose between extractors, or merge their semantic outputs. An independently
deployed GPU service supplies inference; the Agentic Core owns selection/fusion.
This follows the [project constitution](constitution.md), principles 1, 4 and 5.

## Enable

Merge the entry in `providers.teleocr.example.yaml` into the deployment's provider
configuration and set `TELEOCR_SERVE_URL`. The default provider configuration is
unchanged. Select `provider=teleocr` explicitly in a structure-extraction request.
Image-only support is deliberate; render PDF pages through an existing capability.

```bash
curl -X POST "$TOOLBOX_URL/v1/capabilities/document.structure.extract:execute" \
  -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F 'provider=teleocr' -F 'file=@page.png;type=image/png'
```

The backend must implement:

- `GET /health`: successful status when inference is ready.
- `GET /version`: JSON with `version`, `model` and `model_revision`. Model identity
  participates in cache fingerprinting; do not reuse a revision after changing weights.
  Configured inference defaults also participate in the identity. If identity is
  unavailable, cached output is not reused; inference without a reported model
  revision is rejected.
- `POST /predict`: multipart `file` plus a JSON-encoded `parameters` form field
  containing `min_long`, `max_long`, `batch_size`. Return one page:

```json
{
  "size": [1000, 2000],
  "infer_size": [1200, 2400],
  "blocks": [
    {"type": "text", "bbox": [0.1, 0.2, 0.8, 0.3], "angle": 90, "content": "Example"}
  ]
}
```

`size` is the original image size. Boxes are normalized in that image and are
converted to original pixel coordinates with `TOPLEFT` origin. Crop `angle` is
retained as metadata; normalization does not silently rotate source-page geometry.
Unknown labels and image-only pages are retained. Confidence remains `null` when
the backend supplies no calibrated confidence. HTML tables and formula content
remain available in the canonical document. Invalid geometry/response shapes fail
explicitly rather than silently becoming flattened text.

## Selection and evaluation

Keep choices explicit: existing Docling/MinerU, TeleOCR, or multi-provider fusion
in the Agentic Core. Benchmark ordering conventions belong to a benchmark renderer,
not to the canonical accessibility tree. Learned selection thresholds remain
experimental; a higher average score is insufficient to enable automatic routing.

Observed failure modes motivate optional use: TeleOCR can omit text inside images,
split verses/lists excessively, or hallucinate rotated marginal text. Generic
fusion can also join catalog units incorrectly or mishandle rotated-page geometry.
Preserve formulas and provider provenance when evaluating hybrid policies.

The adapter is tested with simulated HTTP transport through the real normalization
builder. Benchmark extraction and endpoint deployment are separate validations.
Public experiment reports must include dataset/code revisions, per-component
coverage, paired comparisons, subgroup counts, uncertainty and failures. Never
publish benchmark images, annotations, extracted text or private dataset artifacts.
