"""Piper neural TTS sidecar server.

Provides a fast HTTP API for local, offline text-to-speech synthesis via Piper and ONNX.
Produces standard WAV audio streams for consumption by the Acessilia Toolbox.
"""

from __future__ import annotations

import io
import logging
import os
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Response
from pydantic import BaseModel

LOG = logging.getLogger("piper_tts_serve")
logging.basicConfig(level=logging.INFO)

try:
    import importlib.metadata
    PIPER_VERSION = importlib.metadata.version("piper-tts")
except Exception:
    PIPER_VERSION = "1.2.0"

MODELS_DIR = Path(os.getenv("PIPER_MODELS_DIR", "/models"))
DEFAULT_VOICE = os.getenv("PIPER_DEFAULT_VOICE", "pt_BR-faber-medium")

_loaded_voices: dict[str, Any] = {}


def _get_voice(voice_name: str) -> Any:
    """Retrieve or load a PiperVoice model instance from the models directory."""
    if voice_name in _loaded_voices:
        return _loaded_voices[voice_name]

    try:
        from piper.voice import PiperVoice
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="piper-tts library not installed") from exc

    onnx_path = MODELS_DIR / f"{voice_name}.onnx"
    json_path = MODELS_DIR / f"{voice_name}.onnx.json"

    if not onnx_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Voice model '{voice_name}' not found at {onnx_path}. "
                f"Ensure it is present in {MODELS_DIR}."
            ),
        )

    config_path = str(json_path) if json_path.is_file() else None
    try:
        voice = PiperVoice.load(str(onnx_path), config_path=config_path)
        _loaded_voices[voice_name] = voice
        LOG.info("Loaded voice model '%s' from %s", voice_name, onnx_path)
        return voice
    except Exception as exc:
        LOG.exception("Failed to load voice '%s': %s", voice_name, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load voice model '{voice_name}': {exc}",
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info("Starting Piper TTS sidecar (models dir: %s)...", MODELS_DIR)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    _loaded_voices.clear()


app = FastAPI(title="piper-tts", lifespan=lifespan)


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    speaker_id: int | None = None
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.2.0", "models_dir": str(MODELS_DIR)}


@app.get("/version")
def version():
    return {
        "version": "1.2.0",
        "piper": PIPER_VERSION,
        "engine": "piper-onnx",
        "default_voice": DEFAULT_VOICE,
    }


@app.post("/synthesize")
def synthesize(
    text: Annotated[str, Form()],
    voice: Annotated[str, Form()] = DEFAULT_VOICE,
    speaker_id: Annotated[int | None, Form()] = None,
    length_scale: Annotated[float | None, Form()] = None,
    noise_scale: Annotated[float | None, Form()] = None,
    noise_w: Annotated[float | None, Form()] = None,
):
    clean_text = text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    piper_voice = _get_voice(voice)

    try:
        from piper.config import SynthesisConfig
        syn_config = SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w,
        )
    except Exception:
        syn_config = None

    wav_io = io.BytesIO()
    try:
        with wave.open(wav_io, "wb") as wav_file:
            piper_voice.synthesize_wav(clean_text, wav_file, syn_config=syn_config)
    except Exception as exc:
        LOG.exception("Synthesis error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {exc}") from exc

    return Response(content=wav_io.getvalue(), media_type="audio/wav")
