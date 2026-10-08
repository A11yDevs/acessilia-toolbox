"""RapidOCR orientation detection and page correction sidecar server.

Implements two-stage page orientation detection (0°, 90°, 180°, 270°) and correction.
Stage 1: Detect bounding boxes at 0°. If majority of boxes have H/W > 1.5, candidate 90°/270°.
Stage 2: Evaluate OCR confidence and text score on downscaled image (<= 1024px) at 90° vs 270° (and 180°).
"""

from __future__ import annotations

import base64
import io
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image

try:
    import importlib.metadata
    RAPIDOCR_VERSION = importlib.metadata.version("rapidocr_onnxruntime")
except Exception:
    RAPIDOCR_VERSION = "1.3.0"

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None  # type: ignore[assignment, misc]

LOG = logging.getLogger("rapidocr_orient_serve")
logging.basicConfig(level=logging.INFO)

state: dict[str, Any] = {
    "engine": None,
    "model_loaded": False,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info("Initializing RapidOCR orientation engine...")
    try:
        if RapidOCR is not None:
            # Initialize RapidOCR with direction classifier enabled
            engine = RapidOCR()
            state["engine"] = engine
            state["model_loaded"] = True
            LOG.info("RapidOCR engine initialized successfully.")
        else:
            LOG.warning("RapidOCR package not available in environment.")
    except Exception as exc:
        LOG.exception("Failed to initialize RapidOCR: %s", exc)
        state["model_loaded"] = False
    yield
    state.clear()


app = FastAPI(title="rapidocr-orient", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if state["model_loaded"] else "degraded",
        "engine_ready": state["model_loaded"],
    }


@app.get("/version")
def version() -> dict[str, str]:
    return {
        "version": "1.0.0",
        "engine": "rapidocr_onnxruntime",
        "engine_version": RAPIDOCR_VERSION,
        "capability": "image.page.orient",
    }


def _downscale_if_needed(image: Image.Image, max_dim: int = 1024) -> Image.Image:
    w, h = image.size
    if max(w, h) <= max_dim:
        return image
    ratio = max_dim / float(max(w, h))
    new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
    return image.resize((new_w, new_h), Image.Resampling.BILINEAR)


def _detect_orientation(engine: Any, image: Image.Image) -> tuple[int, float, str]:
    """Two-stage orientation detection algorithm.

    Returns:
        (detected_angle, confidence, stage_description)
        detected_angle is 0, 90, 180, or 270 (degrees clockwise to rotate to make upright).
    """
    if engine is None:
        return 0, 0.0, "engine_unavailable"

    # Stage 1: Run detection at 0° on downscaled image
    downscaled = _downscale_if_needed(image, max_dim=1024)
    downscaled_rgb = downscaled.convert("RGB")
    
    import numpy as np
    img_np = np.array(downscaled_rgb)

    # RapidOCR returns: result, elapse_list
    # result: list of [dt_boxes, rec_res, score]
    results, _ = engine(img_np)
    if not results:
        return 0, 0.5, "stage1_no_text"

    tall_boxes = 0
    total_boxes = len(results)
    total_conf = 0.0

    for item in results:
        box = item[0]  # 4 points: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        score = item[2]
        total_conf += float(score) if score is not None else 0.5
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        bw = max(xs) - min(xs)
        bh = max(ys) - min(ys)
        if bw > 0 and (bh / bw) > 1.5:
            tall_boxes += 1

    mean_conf_0 = total_conf / max(1, total_boxes)
    tall_ratio = tall_boxes / max(1, total_boxes)

    # If less than 40% tall boxes, assume upright (0°)
    if tall_ratio < 0.40:
        return 0, mean_conf_0, "stage1_upright"

    # Stage 2: Tall boxes dominant (H/W > 1.5). Evaluate 90° vs 270° (and 180°)
    candidates = [90, 270, 180]
    best_angle = 0
    best_score = mean_conf_0
    best_count = total_boxes

    for angle in candidates:
        rotated = downscaled_rgb.rotate(angle, expand=True)
        rot_np = np.array(rotated)
        rot_res, _ = engine(rot_np)
        if not rot_res:
            continue
        rot_count = len(rot_res)
        rot_conf = sum(float(item[2]) for item in rot_res if item[2] is not None) / max(1, rot_count)
        # Check that boxes at this angle are actually horizontal (W > H)
        wide_boxes = sum(
            1 for item in rot_res
            if (max(p[0] for p in item[0]) - min(p[0] for p in item[0])) >=
               (max(p[1] for p in item[0]) - min(p[1] for p in item[0]))
        )
        wide_ratio = wide_boxes / max(1, rot_count)
        composite_score = rot_conf * wide_ratio * (rot_count / max(1, total_boxes))

        if composite_score > best_score:
            best_score = composite_score
            best_angle = angle
            best_count = rot_count

    return best_angle, round(float(best_score), 4), "stage2_evaluated"


@app.post("/orient")
async def orient_page(
    file: Annotated[UploadFile, File(description="Page image")],
    return_image: Annotated[bool, Form(description="Whether to include rotated image payload")] = False,
) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty image payload")

    try:
        image = Image.open(io.BytesIO(content))
        orig_w, orig_h = image.size
        orig_format = image.format or "PNG"
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot decode image: {exc}") from exc

    engine = state.get("engine")
    angle, conf, stage = _detect_orientation(engine, image)

    response: dict[str, Any] = {
        "angle": angle,
        "confidence": conf,
        "stage": stage,
        "original_width": orig_w,
        "original_height": orig_h,
        "auto_rotated": angle != 0,
    }

    if angle != 0:
        # Rotate image to make it upright
        rotated_img = image.rotate(angle, expand=True)
        response["oriented_width"] = rotated_img.size[0]
        response["oriented_height"] = rotated_img.size[1]
        if return_image:
            buf = io.BytesIO()
            save_format = orig_format if orig_format.upper() in {"PNG", "JPEG", "TIFF", "WEBP"} else "PNG"
            rotated_img.save(buf, format=save_format)
            response["image_base64"] = base64.b64encode(buf.getvalue()).decode("ascii")
            response["media_type"] = f"image/{save_format.lower()}"
    else:
        response["oriented_width"] = orig_w
        response["oriented_height"] = orig_h
        if return_image:
            response["image_base64"] = base64.b64encode(content).decode("ascii")
            response["media_type"] = file.content_type or "image/png"

    return response
