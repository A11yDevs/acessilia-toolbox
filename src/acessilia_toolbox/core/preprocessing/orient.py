"""Image preprocessing for page orientation.

The orientation decision is made by an out-of-process provider; this module only
declares the request/response shape and applies the returned image. Keeping the
raster decoding in the sidecar is what lets the core package stay free of imaging
runtimes.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger(__name__)

SUPPORTED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
    "image/webp",
}


@dataclass(frozen=True)
class OrientationResult:
    angle: int  # 0, 90, 180, 270
    confidence: float
    stage: str
    auto_rotated: bool
    original_dimensions: tuple[int, int]
    oriented_dimensions: tuple[int, int]


def is_supported_image(media_type: str) -> bool:
    """Returns True if the media type is a raster image supported for orientation."""
    return media_type.lower() in SUPPORTED_IMAGE_TYPES


def _as_dimension(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _decode_oriented_image(document: dict[str, Any]) -> bytes | None:
    """Decode the oriented payload the sidecar optionally returns."""
    encoded = document.get("image_base64")
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        LOG.warning("orientation provider returned undecodable image payload")
        return None
    return decoded or None


def detect_and_orient_image(
    payload: bytes,
    media_type: str = "image/png",
    orient_provider: Any = None,
) -> tuple[bytes, OrientationResult]:
    """Detect the natural reading orientation and return an upright payload.

    The provider reports the clockwise angle and, when asked, the rotated image.
    If it cannot be reached the original payload is returned unchanged so an
    optional preprocessing step never fails an extraction.

    Returns:
        (oriented_payload, orientation_result)
    """
    if not is_supported_image(media_type):
        return payload, OrientationResult(
            angle=0,
            confidence=0.0,
            stage="unsupported_media_type",
            auto_rotated=False,
            original_dimensions=(0, 0),
            oriented_dimensions=(0, 0),
        )

    if orient_provider is None:
        return payload, OrientationResult(
            angle=0,
            confidence=0.0,
            stage="no_provider",
            auto_rotated=False,
            original_dimensions=(0, 0),
            oriented_dimensions=(0, 0),
        )

    try:
        extraction = orient_provider.execute(
            "image.page.orient",
            payload,
            filename="page_image",
            media_type=media_type,
            parameters={"return_image": True},
        )
    except Exception:
        LOG.warning(
            "orientation preprocessing failed; using the unrotated image",
            exc_info=True,
        )
        return payload, OrientationResult(
            angle=0,
            confidence=0.0,
            stage="provider_failed",
            auto_rotated=False,
            original_dimensions=(0, 0),
            oriented_dimensions=(0, 0),
        )

    document = extraction.document if isinstance(extraction.document, dict) else {}
    angle = _as_dimension(document.get("angle", 0)) % 360
    confidence = float(document.get("confidence") or 0.0)
    stage = str(document.get("stage", "provider"))
    original = (
        _as_dimension(document.get("original_width")),
        _as_dimension(document.get("original_height")),
    )

    if angle == 0:
        LOG.info(
            "page orientation detected as upright (stage=%s confidence=%.4f)",
            stage,
            confidence,
        )
        return payload, OrientationResult(
            angle=0,
            confidence=confidence,
            stage=stage,
            auto_rotated=False,
            original_dimensions=original,
            oriented_dimensions=original,
        )

    oriented_payload = _decode_oriented_image(document)
    if oriented_payload is None:
        LOG.warning(
            "orientation provider reported angle=%s without an oriented image; "
            "extraction continues unrotated",
            angle,
        )
        return payload, OrientationResult(
            angle=0,
            confidence=confidence,
            stage=f"{stage}_image_unavailable",
            auto_rotated=False,
            original_dimensions=original,
            oriented_dimensions=original,
        )

    oriented = (
        _as_dimension(document.get("oriented_width")),
        _as_dimension(document.get("oriented_height")),
    )
    LOG.info(
        "page orientation corrected by %s degrees (stage=%s confidence=%.4f)",
        angle,
        stage,
        confidence,
    )
    return oriented_payload, OrientationResult(
        angle=angle,
        confidence=confidence,
        stage=stage,
        auto_rotated=True,
        original_dimensions=original,
        oriented_dimensions=oriented,
    )
