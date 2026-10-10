# RapidOCR Page Orientation Provider

The `image.page.orient` capability detects the natural reading orientation of an image
page (0°, 90°, 180°, 270°) and optionally returns the oriented image rotated clockwise.

## Architecture

Following the [project constitution](constitution.md), the Toolbox remains 100% lightweight
with zero heavy ML dependencies in its Python environment. Page orientation detection is executed
via a dedicated sidecar container running RapidOCR with ONNX Runtime (`rapidocr-orient`).

- **Sidecar Dockerfile**: `docker/rapidocr-orient.Dockerfile`
- **Sidecar Service**: `docker/rapidocr-orient/server.py`
- **Compose Definition**: `docker-compose.yml` (`rapidocr-orient:5004`)
- **Capability Schema**: `capabilities/image.page.orient.yaml`
- **Provider Adapter**: `src/acessilia_toolbox/providers/rapidocr_orient.py`

## Usage

### Direct Capability Execution

```bash
curl -X POST "$TOOLBOX_URL/v1/capabilities/image.page.orient:execute" \
  -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F 'provider=rapidocr-orient' \
  -F 'file=@page.jpg;type=image/jpeg'
```

Response JSON:
```json
{
  "detected_angle": 90,
  "confidence": 0.95,
  "oriented": true,
  "width": 1119,
  "height": 666
}
```

### Preprocessing via Auto-Rotate Flag

When extracting document structures with `auto_rotate=True`, the executor automatically queries
`image.page.orient` (if available and media is an image) and feeds the correctly oriented image
into downstream layout engines (Docling, MinerU, TeleOCR).
