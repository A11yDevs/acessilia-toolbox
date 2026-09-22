"""RapidLaTeXOCR standalone sidecar server.

Provides an HTTP API for lightweight mathematical formula recognition from image chips.
"""

from __future__ import annotations

import importlib.metadata
import io
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from rapid_latex_ocr import LaTeXOCR

LOG = logging.getLogger("rapid_latex_ocr_serve")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Upstream Monkey Patch
# RapidLaTeXOCR (v0.1.1+) contains an issue in LaTeXOCR.loop_image_resizer where
# on images with certain aspect ratios, `np.argmax(resizer_res, axis=-1)` returns
# a 1D numpy array instead of a scalar, causing `int()` conversion to fail with:
# "TypeError: only 0-dimensional arrays can be converted to Python scalars".
# We patch loop_image_resizer with np.squeeze to ensure scalar extraction.
# ---------------------------------------------------------------------------
def _patched_loop(self: Any, img: np.ndarray) -> np.ndarray:
    pillow_img = Image.fromarray(img)
    pad_img = self.pre_pro.pad(pillow_img)
    input_image = self.pre_pro.minmax_size(pad_img).convert("RGB")
    r, w, h = 1.0, float(input_image.size[0]), float(input_image.size[1])
    for _ in range(10):
        h = int(h * r)
        final_img, pad_img = self.pre_process(input_image, r, int(w), int(h))
        resizer_res = self.image_resizer([final_img.astype(np.float32)])[0]
        argmax_idx = int(np.squeeze(np.argmax(resizer_res, axis=-1)))
        w = (argmax_idx + 1) * 32
        if w == pad_img.size[0]:
            break
        r = w / pad_img.size[0]
    return final_img

LaTeXOCR.loop_image_resizer = _patched_loop

state: dict[str, Any] = {
    "model": None,
    "model_loaded": False,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info("Initializing RapidLaTeXOCR model...")
    try:
        model = LaTeXOCR()
        state["model"] = model
        state["model_loaded"] = True
        LOG.info("RapidLaTeXOCR model initialized successfully.")
    except Exception as exc:
        LOG.exception("Failed to initialize RapidLaTeXOCR model: %s", exc)
        state["model_loaded"] = False
    yield
    state.clear()


app = FastAPI(title="rapid-latex-ocr", lifespan=lifespan)


@app.get("/health")
def health():
    if not state.get("model_loaded"):
        raise HTTPException(status_code=503, detail="Model is not ready")
    return {"status": "ok", "version": "0.1.0"}


@app.get("/version")
def version():
    try:
        pkg_version = importlib.metadata.version("rapid_latex_ocr")
    except Exception:
        pkg_version = "0.1.1"
    return {
        "version": "0.1.0",
        "rapid_latex_ocr": pkg_version,
    }


@app.post("/predict")
async def predict(file: Annotated[UploadFile, File()]):
    model = state.get("model")
    if model is None or not state.get("model_loaded"):
        raise HTTPException(status_code=503, detail="Model is not ready")

    try:
        contents = await file.read()
        raw_img = Image.open(io.BytesIO(contents))

        # Robust preprocessing: compose transparency onto white background
        if raw_img.mode in ("RGBA", "LA") or (
            raw_img.mode == "P" and "transparency" in raw_img.info
        ):
            raw_rgba = raw_img.convert("RGBA")
            bg = Image.new("RGBA", raw_rgba.size, (255, 255, 255, 255))
            alpha_comp = Image.alpha_composite(bg, raw_rgba).convert("RGB")
        else:
            alpha_comp = raw_img.convert("RGB")

        # Add 20px padding (respiro) to prevent glyphs abutting image borders
        padded = Image.new(
            "RGB", (alpha_comp.width + 40, alpha_comp.height + 40), (255, 255, 255)
        )
        padded.paste(alpha_comp, (20, 20))

        # Pass numpy array directly to model
        img_np = np.array(padded)
        res, elapse = model(img_np)

        # Note: RapidLaTeXOCR does not output token-level probability/confidence scores.
        # We omit the confidence field to adhere faithfully to artifact/latex@1.
        return {
            "latex": res,
            "elapse": elapse,
            "bbox": [0, 0, raw_img.width, raw_img.height],
        }
    except Exception as exc:
        LOG.exception("Inference error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
