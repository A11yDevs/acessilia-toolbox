# RapidOCR Page Orientation Provider

The `image.page.orient` capability detects the natural reading orientation of an
image page (0°, 90°, 180°, 270°) and optionally returns the upright image.

## Architecture

Following the [project constitution](constitution.md), the Toolbox remains free
of imaging and ML runtimes. Page orientation detection runs in a dedicated
sidecar container with RapidOCR and ONNX Runtime (`rapidocr-orient`), matching
the pattern used by `docling-serve` and `mineru-api`.

- **Sidecar Dockerfile**: `docker/rapidocr-orient.Dockerfile`
- **Sidecar service**: `docker/rapidocr-orient/server.py`
- **Compose definition**: `docker-compose.yml` (`rapidocr-orient:5006`)
- **Capability manifest**: `capabilities/image.page.orient.yaml`
- **Provider adapter**: `src/acessilia_toolbox/providers/rapidocr_orient.py`

## Enabling

The provider is registered in `providers-config.yaml` and is inactive until the
sidecar is reachable. Set `RAPIDOCR_ORIENT_URL` to override the endpoint; the
default resolves to the Compose service name.

```bash
docker compose up -d rapidocr-orient
curl -fsS http://localhost:5006/health
```

`GET /health` returns `503` until the OCR engine is loaded, so Compose never
reports a model-less sidecar as healthy.

## Direct capability execution

```bash
curl -X POST "$TOOLBOX_URL/v1/capabilities/image.page.orient:execute" \
  -H "Authorization: ******" \
  -F 'provider=rapidocr-orient' \
  -F 'file=@page.jpg;type=image/jpeg'
```

The document conforms to `artifact/page-orientation@1`:

```json
{
  "angle": 90,
  "confidence": 0.91,
  "stage": "stage2_evaluated",
  "auto_rotated": true,
  "original_width": 1200,
  "original_height": 800,
  "oriented_width": 800,
  "oriented_height": 1200
}
```

`angle` is the clockwise rotation required to make the page upright. When the
request asks for the corrected image (`return_image`), the payload also carries
`image_base64` and `media_type`.

## Detection strategy

- **Stage 1** reads the page once. The OCR angle classifier settles the 180°
  case, which keeps text horizontal and is therefore invisible to geometry
  heuristics. Upright pages are reported as `stage1_upright`.
- **Stage 2** compares how well the page reads at 0° versus 90° and 270°. Each
  pass scores horizontal text lines by their recognition confidence, so a
  quarter-turned page — whose crops are vertical or unreadable — loses.

## Preprocessing during extraction

Passing `parameters={"auto_rotate": true}` makes the executor query
`image.page.orient` for image inputs and feed the upright image to the downstream
layout engine (Docling, MinerU, TeleOCR). The step is opt-in and off by default.

The decision is recorded in `provenance.orientation`:

```json
{
  "angle": 90,
  "confidence": 0.91,
  "stage": "stage2_evaluated",
  "auto_rotated": true,
  "original_dimensions": [1200, 800],
  "oriented_dimensions": [800, 1200]
}
```

If the sidecar is unreachable, the extraction proceeds with the original image and
the reason is recorded (`provider_failed`). The rotated bytes become the input
fingerprint, so a cached result always matches the payload actually processed.
