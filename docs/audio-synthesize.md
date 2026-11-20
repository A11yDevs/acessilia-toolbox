# Audio Synthesize Capability (`audio.synthesize`)

> A Brazilian Portuguese translation is available in [audio-synthesize.pt-br.md](audio-synthesize.pt-br.md).

The `audio.synthesize` capability converts plain text or markdown content into
spoken audio artifacts (`audio/mpeg` or `audio/wav`), providing accessible spoken
representations of documents.

## Overview

Following the architectural principles of the Acessilia ecosystem:
- **Principle 4 of the Constitution (Provider Isolation):** Lightweight cloud or standard
  libraries run in-process, while heavy neural engines and machine learning runtimes
  run out-of-process in dedicated sidecars.
- **Principle 5 (Artifact Reference Travel):** Synthesized binary audio is stored
  directly in the configured artifact store (MinIO or local filesystem fallback).
  The capability response returns an immutable artifact reference (`audio-export@1`),
  never inlining audio bytes into API or MCP payloads.
- **ADR-0003 & Issue #73:** Eliminates the operational deviation of in-process audio
  synthesis inside the agentic core, centralizing execution in the Toolbox.

---

## Providers

| Provider | Transport | Output Format | Offline / Local | Engine |
|---|---|---|---|---|
| `edge-tts` | `in_process` | `audio/mpeg` (MP3) | No (Cloud API) | Microsoft Edge Speech Service |
| `piper-tts` | `http` (sidecar) | `audio/wav` (WAV) | Yes (100% Offline) | Piper ONNX Runtime |

### 1. `edge-tts` (In-Process)

`edge-tts` communicates with Microsoft's speech service via WebSockets, requiring
no local neural model weights (~70KB Python package).

Install with the optional `audio` extra:

```bash
pip install "acessilia-toolbox[audio]"
```

**Features:**
- Paragraph-level chunking (default 1500 characters) with sentence boundary splitting
  for oversized paragraphs.
- Asynchronous chunk synthesis with bounded concurrency (`concurrency=5`).
- Concatenation of audio parts into a unified MP3 stream.

**Parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `voice` | `str` | `pt-BR-FranciscaNeural` | Voice identifier (e.g. `pt-BR-AntonioNeural`, `en-US-AriaNeural`) |
| `language` | `str` | `pt-BR` | Language code (e.g. `pt-BR`, `en-US`) |
| `rate` | `str \| float` | `null` | Speed adjustment (e.g. `"+20%"`, `1.2`, `"-10%"`) |
| `volume` | `str \| float` | `null` | Volume adjustment (e.g. `"+10%"`, `0.8`) |
| `pitch` | `str \| int` | `null` | Pitch adjustment (e.g. `"+50Hz"`, `"-20Hz"`) |
| `chunk_size` | `int` | `1500` | Maximum character length per text chunk |
| `concurrency` | `int` | `5` | Maximum concurrent chunk synthesis requests |

---

### 2. `piper-tts` (Docker HTTP Sidecar)

`piper-tts` provides local, low-latency, offline neural text-to-speech using ONNX
Runtime (< 0.1 Real-Time Factor on modern CPUs).

Per Principle 4, it runs as an isolated HTTP sidecar service:
- **Dockerfile:** `docker/piper-tts.Dockerfile`
- **Default Port:** `5012`
- **Default Model:** `pt_BR-faber-medium` (downloaded during build and cached in `/models`)
- **Published Image:** `ghcr.io/a11ydevs/acessilia-toolbox-piper-tts`

**Configuration (`.env`):**

```bash
PIPER_TTS_URL=http://piper-tts:5012
PIPER_TTS_PORT=5012
# PIPER_TTS_IMAGE=ghcr.io/a11ydevs/acessilia-toolbox-piper-tts:develop
```

**Parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `voice` | `str` | `pt_BR-faber-medium` | Voice model name in `/models` (e.g. `pt_BR-edresson-low`) |
| `language` | `str` | `pt-BR` | Language tag |
| `speaker_id` | `int` | `null` | Multi-speaker voice ID (for voices with multiple speakers) |
| `length_scale` | `float` | `null` | Speed/duration scale (e.g. `1.1` for slower, `0.9` for faster) |
| `noise_scale` | `float` | `null` | Phoneme noise scale (default `0.667`) |
| `noise_w` | `float` | `null` | Phoneme width noise scale (default `0.8`) |

---

## Output Schema (`schemas/audio-export@1.json`)

The output metadata complies with `schemas/audio-export@1.json`:

```json
{
  "artifact_id": "sha256:d8a2...3f",
  "media_type": "audio/mpeg",
  "size_bytes": 105432,
  "duration_seconds": 12.45,
  "voice": "pt-BR-FranciscaNeural",
  "language": "pt-BR",
  "chunk_count": 3,
  "toolchain": {
    "provider": "edge-tts",
    "edge_tts": "6.1.12"
  }
}
```

---

## API Execution Example

Execute via REST:

```bash
curl -X POST http://localhost:8000/v1/capabilities/audio.synthesize:execute \
  -F "file=@capitulo1.txt;type=text/plain" \
  -F 'parameters={"voice":"pt_BR-faber-medium"}' \
  -F "provider_id=piper-tts"
```

The response contains the execution metadata and the binary artifact ID. The audio
file can then be retrieved via `/v1/artifacts/{artifact_id}`.
