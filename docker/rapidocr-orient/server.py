"""RapidOCR orientation detection and page correction sidecar server.

Detects the natural reading rotation of a page image (0°, 90°, 180°, 270°) and
optionally returns the upright image.

Stage 1 reads the page once; the OCR angle classifier settles the 180° case,
which keeps text horizontal and is therefore invisible to geometry heuristics.
Stage 2 compares how well the page reads at 0° versus 90° and 270°.

Convention:
All reported angles (0, 90, 180, 270) represent the clockwise rotation required
to make the image upright. PIL's `Image.rotate` turns counter-clockwise, so
rotating clockwise by `angle` is `image.rotate(360 - angle)`.
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
    "engine_ready": False,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info("Initializing RapidOCR orientation engine...")
    try:
        if RapidOCR is not None:
            engine = RapidOCR()
            state["engine"] = engine
            state["engine_ready"] = True
            LOG.info("RapidOCR engine initialized successfully.")
        else:
            LOG.warning("RapidOCR package not available in environment.")
    except Exception as exc:
        LOG.exception("Failed to initialize RapidOCR: %s", exc)
        state["engine_ready"] = False
    yield
    state.clear()


app = FastAPI(title="rapidocr-orient", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    # Mirror math-ocr: an unready engine must fail `curl -fsS` with 503 so
    # Compose never marks a model-less sidecar as healthy.
    if not state["engine_ready"]:
        raise HTTPException(status_code=503, detail="Model is not ready")
    return {"status": "ok", "engine_ready": True}


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


def _rotate_clockwise(image: Image.Image, angle: int) -> Image.Image:
    """Rotates image clockwise by angle degrees (0, 90, 180, 270)."""
    if angle % 360 == 0:
        return image
    return image.rotate(360 - (angle % 360), expand=True)


def _recognize(engine: Any, image: Image.Image) -> list[Any]:
    """Run one detection+recognition pass, returning the OCR line results."""
    import numpy as np

    results, _ = engine(np.array(image.convert("RGB")))
    return list(results) if results else []


def _text_line_score(results: list[Any]) -> float:
    """Score a pass by how confidently it reads as horizontal text lines.

    Each box contributes its recognition confidence only when it looks like a
    horizontal text line (wider than tall). A rotated page yields vertically
    stacked or unreadable crops, so both its confidence and its wide-box ratio
    fall, pushing the score down. The mean is never inflated by an empty pass.
    """
    if not results:
        return 0.0
    total = 0.0
    for item in results:
        box = item[0]
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        if width <= 0 or width < height:
            continue
        score = item[2]
        total += float(score) if score is not None else 0.5
    return total / len(results)


def _classifier_upside_down_confidence(engine: Any, results: list[Any]) -> float | None:
    """Ask the OCR angle classifier whether the detected lines are upside down.

    PaddleOCR's classifier labels a crop `0` or `180`. Reusing the engine's own
    crops keeps this to one extra inference batch and makes the 180° case, which
    keeps text horizontal and therefore invisible to the geometry heuristics,
    directly observable.

    Returns the mean confidence of the votes when the majority are upside down,
    otherwise `None`.
    """
    classifier = getattr(engine, "text_cls", None)
    engine_loaded = getattr(engine, "use_cls", False)
    if classifier is None or not engine_loaded:
        return None

    import numpy as np

    crops = [np.array(item[1]) for item in results if item[1] is not None]
    crops = [crop for crop in crops if crop.ndim == 3 and min(crop.shape[:2]) > 0]
    if not crops:
        return None

    try:
        _, labels, _ = classifier(crops)
    except Exception as exc:  # pragma: no cover - defensive around native code
        LOG.warning("Angle classifier failed: %s", exc)
        return None

    threshold = float(getattr(classifier, "cls_thresh", 0.9))
    votes = [
        float(score)
        for label, score in labels
        if "180" in str(label) and float(score) > threshold
    ]
    if len(votes) * 2 <= len(crops):
        return None
    return sum(votes) / len(votes)


def _detect_orientation(engine: Any, image: Image.Image) -> tuple[int, float, str]:
    """Detect the clockwise rotation that makes the page upright.

    Stage 1 reads the page once and asks the OCR angle classifier about 180°,
    which horizontal geometry cannot reveal. Stage 2 compares how well the page
    reads at 0° versus 90° and 270° for the rotations that exchange width and
    height.

    Returns:
        (detected_angle, confidence, stage_description)
        detected_angle is 0, 90, 180, or 270 degrees clockwise to make it upright.
    """
    if engine is None:
        return 0, 0.0, "engine_unavailable"

    # Downscaling bounds the cost: orientation is a page-level decision and the
    # text layout it depends on survives the resize.
    downscaled = _downscale_if_needed(image, max_dim=1024)
    downscaled_rgb = downscaled.convert("RGB")

    results = _recognize(engine, downscaled_rgb)
    if not results:
        return 0, 0.0, "no_text_detected"

    upside_down_confidence = _classifier_upside_down_confidence(engine, results)
    if upside_down_confidence is not None:
        return 180, round(float(upside_down_confidence), 4), "stage1_classifier_180"

    score_0 = _text_line_score(results)
    best_angle = 0
    best_score = score_0

    # Only 90° and 270° are compared: a quarter turn is the case the geometry
    # heuristics can detect, and 180° is already settled by the classifier.
    for angle in (90, 270):
        rotated = _rotate_clockwise(downscaled_rgb, angle)
        score = _text_line_score(_recognize(engine, rotated))
        if score > best_score:
            best_score = score
            best_angle = angle

    if best_angle == 0:
        return 0, round(float(score_0), 4), "stage1_upright"
    return best_angle, round(float(best_score), 4), "stage2_evaluated"


@app.post("/orient")
async def orient_page(
    file: Annotated[UploadFile, File(description="Page image")],
    return_image: Annotated[
        bool, Form(description="Whether to include the rotated image payload")
    ] = False,
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
    if engine is None or not state.get("engine_ready"):
        raise HTTPException(status_code=503, detail="Model is not ready")

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
        # Rotate image clockwise to make it upright
        rotated_img = _rotate_clockwise(image, angle)
        response["oriented_width"] = rotated_img.size[0]
        response["oriented_height"] = rotated_img.size[1]
        if return_image:
            buf = io.BytesIO()
            valid_formats = {"PNG", "JPEG", "TIFF", "WEBP"}
            save_format = orig_format if orig_format.upper() in valid_formats else "PNG"
            if save_format.upper() == "JPEG" and rotated_img.mode in ("RGBA", "P"):
                rotated_img = rotated_img.convert("RGB")
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
