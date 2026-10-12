"""Image preprocessing and rotation utilities."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image


@dataclass(frozen=True)
class OrientationResult:
    angle: int  # 0, 90, 180, 270
    confidence: float
    stage: str
    auto_rotated: bool
    original_dimensions: tuple[int, int]
    oriented_dimensions: tuple[int, int]


def rotate_image_payload(
    payload: bytes,
    angle: int,
    media_type: str = "image/png",
) -> bytes:
    """Rotates raster image bytes by angle degrees clockwise and returns encoded bytes."""
    if angle % 360 == 0:
        return payload

    with Image.open(io.BytesIO(payload)) as img:
        orig_format = img.format or "PNG"
        # PIL rotate is counter-clockwise for positive degrees; 360 - angle rotates clockwise.
        rotated = img.rotate(360 - (angle % 360), expand=True)

        buf = io.BytesIO()
        valid_formats = {"PNG", "JPEG", "TIFF", "WEBP"}
        save_format = orig_format if orig_format.upper() in valid_formats else "PNG"
        if save_format.upper() == "JPEG" and rotated.mode in ("RGBA", "P"):
            rotated = rotated.convert("RGB")
        rotated.save(buf, format=save_format)
        return buf.getvalue()


def is_supported_image(media_type: str) -> bool:
    """Returns True if the media type is a raster image supported for orientation."""
    return media_type.lower() in {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/tiff",
        "image/webp",
    }


def detect_and_orient_image(
    payload: bytes,
    media_type: str = "image/png",
    orient_provider: Any = None,
) -> tuple[bytes, OrientationResult]:
    """Detect orientation and rotate image if needed.

    If an orient_provider is provided, calls its execute method.
    Returns:
        (oriented_payload, orientation_result)
    """
    if not is_supported_image(media_type):
        return payload, OrientationResult(
            angle=0,
            confidence=1.0,
            stage="unsupported_media_type",
            auto_rotated=False,
            original_dimensions=(0, 0),
            oriented_dimensions=(0, 0),
        )

    with Image.open(io.BytesIO(payload)) as img:
        orig_w, orig_h = img.size

    if orient_provider is None:
        return payload, OrientationResult(
            angle=0,
            confidence=1.0,
            stage="no_provider",
            auto_rotated=False,
            original_dimensions=(orig_w, orig_h),
            oriented_dimensions=(orig_w, orig_h),
        )

    try:
        extraction = orient_provider.execute(
            "image.page.orient",
            payload,
            filename="page_image",
            media_type=media_type,
            parameters={"return_image": False},
        )
        data = extraction.document if isinstance(extraction.document, dict) else {}
        angle = int(data.get("angle", 0))
        confidence = float(data.get("confidence", 0.0))
        stage = str(data.get("stage", "provider"))
    except Exception:
        # Graceful fallback: do not fail if provider is temporarily unavailable
        return payload, OrientationResult(
            angle=0,
            confidence=0.0,
            stage="provider_failed",
            auto_rotated=False,
            original_dimensions=(orig_w, orig_h),
            oriented_dimensions=(orig_w, orig_h),
        )

    if angle != 0:
        new_payload = rotate_image_payload(payload, angle, media_type=media_type)
        with Image.open(io.BytesIO(new_payload)) as new_img:
            new_w, new_h = new_img.size
        return new_payload, OrientationResult(
            angle=angle,
            confidence=confidence,
            stage=stage,
            auto_rotated=True,
            original_dimensions=(orig_w, orig_h),
            oriented_dimensions=(new_w, new_h),
        )

    return payload, OrientationResult(
        angle=0,
        confidence=confidence,
        stage=stage,
        auto_rotated=False,
        original_dimensions=(orig_w, orig_h),
        oriented_dimensions=(orig_w, orig_h),
    )
