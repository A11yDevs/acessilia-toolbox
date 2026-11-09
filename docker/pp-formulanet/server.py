"""PP-FormulaNet standalone sidecar server.

Formula chips in, LaTeX out. PaddleOCR's formula recognition module runs
PP-FormulaNet_plus-M locally, behind the same small HTTP API as the other math
OCR sidecars: ``/predict``, ``/health`` and ``/version``.
"""

from __future__ import annotations

import importlib.metadata
import io
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from formula_result import recognized_latex
from paddleocr import FormulaRecognition
from PIL import Image

LOG = logging.getLogger("pp_formulanet_serve")
logging.basicConfig(level=logging.INFO)

SERVER_VERSION = "0.1.0"
MODEL_NAME = os.getenv("PP_FORMULANET_MODEL", "PP-FormulaNet_plus-M")
# CPU-only: the image ships the CPU PaddlePaddle build, so no device knob is
# exposed until a GPU variant actually exists.
DEVICE = "cpu"
# Respiro around the crop: the model truncates glyphs that touch the border.
PADDING = 20

state: dict[str, Any] = {
    "model": None,
    "model_loaded": False,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info("Initializing PP-FormulaNet model (%s)...", MODEL_NAME)
    try:
        model = FormulaRecognition(
            model_name=MODEL_NAME,
            device=DEVICE,
        )
        state["model"] = model
        state["model_loaded"] = True
        LOG.info("PP-FormulaNet model initialized successfully.")
    except Exception as exc:
        # The sidecar stays up and reports 503 so the Toolbox sees an
        # unavailable provider instead of a connection refused.
        LOG.exception("Failed to initialize PP-FormulaNet model: %s", exc)
        state["model_loaded"] = False
    yield
    state.clear()


app = FastAPI(title="pp-formulanet", lifespan=lifespan)


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except Exception:
        return "unknown"


@app.get("/health")
def health():
    if not state.get("model_loaded"):
        raise HTTPException(status_code=503, detail="Model is not ready")
    return {"status": "ok", "version": SERVER_VERSION}


@app.get("/version")
def version():
    """Identity that decides the execution cache key.

    MathJax/MathCAT upgrades already invalidate the math-speech cache; a new
    PaddleOCR or model revision must invalidate formula recognition too.
    """
    return {
        "version": SERVER_VERSION,
        "model": MODEL_NAME,
        "paddleocr": _package_version("paddleocr"),
        "paddlepaddle": _package_version("paddlepaddle"),
        # The UniMERNet decoder runs on it; a pin bump changes output and must
        # invalidate the execution cache key like the Paddle versions above.
        "tokenizers": _package_version("tokenizers"),
        # The decoder's post-processing passes every result through
        # ftfy.fix_text, so its version changes output too.
        "ftfy": _package_version("ftfy"),
    }


def _prepare(contents: bytes) -> tuple[Image.Image, tuple[int, int]]:
    """Flatten transparency onto white and pad, as the chip models expect."""
    raw = Image.open(io.BytesIO(contents))
    original_size = raw.size

    if raw.mode in ("RGBA", "LA") or (raw.mode == "P" and "transparency" in raw.info):
        rgba = raw.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(background, rgba).convert("RGB")
    else:
        rgb = raw.convert("RGB")

    padded = Image.new("RGB", (rgb.width + 2 * PADDING, rgb.height + 2 * PADDING), (255, 255, 255))
    padded.paste(rgb, (PADDING, PADDING))
    return padded, original_size


@app.post("/predict")
async def predict(file: Annotated[UploadFile, File()]):
    model = state.get("model")
    if model is None or not state.get("model_loaded"):
        raise HTTPException(status_code=503, detail="Model is not ready")

    try:
        contents = await file.read()
        padded, original_size = _prepare(contents)

        # PaddleOCR reads numpy inputs in BGR order, like OpenCV.
        image = np.ascontiguousarray(np.asarray(padded)[:, :, ::-1])
        latex = recognized_latex(model.predict(input=image, batch_size=1))
        if not latex:
            raise HTTPException(status_code=422, detail="no_formula_recognized")

        # PP-FormulaNet does not expose a calibrated confidence score, so the
        # field stays absent rather than carrying a fabricated number.
        return {
            "latex": latex,
            "bbox": [0, 0, original_size[0], original_size[1]],
        }
    except HTTPException:
        raise
    except Exception as exc:
        LOG.exception("Inference error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
