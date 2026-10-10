"""Tests for page auto-orientation preprocessing and executor integration."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.preprocessing.orient import (
    detect_and_orient_image,
    is_supported_image,
    rotate_image_payload,
)
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry


def make_test_image(width: int = 100, height: int = 200, color: str = "white") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_is_supported_image() -> None:
    assert is_supported_image("image/png") is True
    assert is_supported_image("IMAGE/JPEG") is True
    assert is_supported_image("image/webp") is True
    assert is_supported_image("application/pdf") is False
    assert is_supported_image("text/plain") is False


def test_rotate_image_payload() -> None:
    original = make_test_image(100, 200)
    # 0 degrees returns payload unmodified
    assert rotate_image_payload(original, 0) == original

    # 90 degrees clockwise rotates 100x200 to 200x100
    rotated_90 = rotate_image_payload(original, 90)
    with Image.open(io.BytesIO(rotated_90)) as img:
        assert img.size == (200, 100)

    # 180 degrees keeps 100x200 dimensions
    rotated_180 = rotate_image_payload(original, 180)
    with Image.open(io.BytesIO(rotated_180)) as img:
        assert img.size == (100, 200)

    # 270 degrees clockwise rotates 100x200 to 200x100
    rotated_270 = rotate_image_payload(original, 270)
    with Image.open(io.BytesIO(rotated_270)) as img:
        assert img.size == (200, 100)


def test_detect_and_orient_without_provider() -> None:
    original = make_test_image(120, 240)
    payload, res = detect_and_orient_image(original, "image/png", orient_provider=None)
    assert payload == original
    assert res.angle == 0
    assert res.auto_rotated is False
    assert res.original_dimensions == (120, 240)
    assert res.oriented_dimensions == (120, 240)


def test_detect_and_orient_with_mock_provider() -> None:
    original = make_test_image(200, 100)

    class MockOrientAdapter:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                document={
                    "angle": 90,
                    "confidence": 0.92,
                    "stage": "stage2_evaluated",
                    "auto_rotated": True,
                }
            )

    payload, res = detect_and_orient_image(
        original, "image/png", orient_provider=MockOrientAdapter()
    )
    assert res.angle == 90
    assert res.auto_rotated is True
    assert res.original_dimensions == (200, 100)
    assert res.oriented_dimensions == (100, 200)
    with Image.open(io.BytesIO(payload)) as img:
        assert img.size == (100, 200)


def test_executor_auto_rotate_integration() -> None:
    capabilities = CapabilityRegistry.from_directory(Path("capabilities"))
    providers = ProviderRegistry([
        ProviderDescriptor(
            id="mock-extractor",
            version="1.0",
            capabilities=["document.structure.extract"],
            media_types=["image/png"],
        ),
        ProviderDescriptor(
            id="rapidocr-orient",
            version="1.0",
            capabilities=["image.page.orient"],
            media_types=["image/png"],
        ),
    ])

    passed_payloads: list[bytes] = []

    class MockExtractor:
        descriptor = providers.get("mock-extractor")

        def execute(self, capability_id: str, payload: bytes, **kwargs: Any) -> ExtractionResult:
            passed_payloads.append(payload)
            from datetime import UTC, datetime

            return ExtractionResult(
                document={"pages": {}, "elements": []},
                backend="mock-extractor",
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                duration_ms=10,
                version="1.0",
            )

        def versions(self) -> dict[str, str]:
            return {"provider": "1.0"}

    class MockOrient:
        descriptor = providers.get("rapidocr-orient")

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                document={
                    "angle": 90,
                    "confidence": 0.95,
                    "stage": "stage2_evaluated",
                    "auto_rotated": True,
                }
            )

        def versions(self) -> dict[str, str]:
            return {"provider": "1.0"}

    def adapter_factory(desc: ProviderDescriptor) -> Any:
        if desc.id == "rapidocr-orient":
            return MockOrient()
        return MockExtractor()

    executor = CapabilityExecutor(capabilities, providers, adapter_factory)
    test_img = make_test_image(200, 100)

    # 1. Without auto_rotate: passes original payload
    executor.execute(
        "document.structure.extract",
        test_img,
        filename="test.png",
        media_type="image/png",
        parameters={"auto_rotate": False},
    )
    assert len(passed_payloads) == 1
    assert passed_payloads[0] == test_img

    # 2. With auto_rotate=True: payload passed to extractor is rotated (100, 200)
    executor.execute(
        "document.structure.extract",
        test_img,
        filename="test.png",
        media_type="image/png",
        parameters={"auto_rotate": True},
    )
    assert len(passed_payloads) == 2
    assert passed_payloads[1] != test_img
    with Image.open(io.BytesIO(passed_payloads[1])) as oriented:
        assert oriented.size == (100, 200)
