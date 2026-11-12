"""Piper neural TTS sidecar server.

Provides a fast HTTP API for local, offline text-to-speech synthesis via Piper and ONNX.
Produces standard WAV audio streams for consumption by the Acessilia Toolbox.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import urllib.request
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Response

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
_model_lock = threading.Lock()
_inference_lock = threading.Lock()

# Mapping of known popular voices to their Hugging Face paths for on-demand downloading
_KNOWN_VOICES: dict[str, str] = {
    "pt_BR-faber-medium": "pt/pt_BR/faber/medium",
    "pt_BR-edresson-low": "pt/pt_BR/edresson/low",
    "en_US-lessac-medium": "en/en_US/lessac/medium",
}


def _download_model_if_missing(voice_name: str, onnx_path: Path, json_path: Path) -> None:
    """Download known model files on demand if missing from MODELS_DIR."""
    if onnx_path.is_file() and json_path.is_file():
        return

    rel_path = _KNOWN_VOICES.get(voice_name)
    if not rel_path:
        return

    base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{rel_path}"
    onnx_url = f"{base_url}/{voice_name}.onnx"
    json_url = f"{base_url}/{voice_name}.onnx.json"

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    LOG.info("Model '%s' not found locally; downloading from %s...", voice_name, base_url)
    try:
        if not onnx_path.is_file():
            tmp_onnx = onnx_path.with_suffix(".tmp")
            urllib.request.urlretrieve(onnx_url, str(tmp_onnx))
            tmp_onnx.replace(onnx_path)
            LOG.info("Downloaded %s", onnx_path.name)
        if not json_path.is_file():
            tmp_json = json_path.with_suffix(".tmp")
            urllib.request.urlretrieve(json_url, str(tmp_json))
            tmp_json.replace(json_path)
            LOG.info("Downloaded %s", json_path.name)
    except Exception as exc:
        LOG.warning("Failed to auto-download voice '%s': %s", voice_name, exc)


def _get_voice(voice_name: str) -> Any:
    """Retrieve or load a PiperVoice model instance from the models directory."""
    with _model_lock:
        if voice_name in _loaded_voices:
            return _loaded_voices[voice_name]

        try:
            from piper.voice import PiperVoice
        except ImportError as exc:
            raise HTTPException(
                status_code=503, detail="piper-tts library not installed"
            ) from exc

        onnx_path = MODELS_DIR / f"{voice_name}.onnx"
        json_path = MODELS_DIR / f"{voice_name}.onnx.json"

        _download_model_if_missing(voice_name, onnx_path, json_path)

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
    with _model_lock:
        _loaded_voices.clear()


app = FastAPI(title="piper-tts", lifespan=lifespan)


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

    syn_config = None
    if any(v is not None for v in (speaker_id, length_scale, noise_scale, noise_w)):
        try:
            from piper.config import SynthesisConfig
            syn_kwargs: dict[str, Any] = {}
            if speaker_id is not None:
                syn_kwargs["speaker_id"] = speaker_id
            if length_scale is not None:
                syn_kwargs["length_scale"] = length_scale
            if noise_scale is not None:
                syn_kwargs["noise_scale"] = noise_scale
            if noise_w is not None:
                syn_kwargs["noise_w_scale"] = noise_w
            syn_config = SynthesisConfig(**syn_kwargs)
        except Exception as exc:
            LOG.warning(
                "Failed to initialize SynthesisConfig: %s; proceeding with voice defaults",
                exc,
            )

    wav_io = io.BytesIO()
    try:
        with _inference_lock, wave.open(wav_io, "wb") as wav_file:
            piper_voice.synthesize_wav(clean_text, wav_file, syn_config=syn_config)
    except Exception as exc:
        LOG.exception("Synthesis error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {exc}") from exc

    return Response(
        content=wav_io.getvalue(),
        media_type="audio/wav",
        headers={
            "X-Piper-Version": PIPER_VERSION,
            "X-Piper-Engine": "piper-onnx",
        },
    )
