"""Page auto-orientation preprocessing and its executor integration."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.preprocessing.orient import (
    detect_and_orient_image,
    is_supported_image,
)
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry

UPRIGHT_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"upright-payload"
).decode("ascii")
ROTATED_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"rotated-payload").decode("ascii")


def make_test_image(width: int = 100, height: int = 200, color: str = "white") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class StubOrientAdapter:
    """Minimal stand-in for the orientation provider adapter."""

    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document
        self.calls: list[dict[str, Any]] = []

    def execute(self, capability_id: str, payload: bytes, **kwargs: Any) -> Any:
        self.calls.append({"capability_id": capability_id, "payload": payload, **kwargs})
        return SimpleNamespace(document=self._document)


def test_is_supported_image() -> None:
    assert is_supported_image("image/png") is True
    assert is_supported_image("IMAGE/JPEG") is True
    assert is_supported_image("image/webp") is True
    assert is_supported_image("application/pdf") is False
    assert is_supported_image("text/plain") is False


def test_detect_and_orient_without_provider() -> None:
    original = make_test_image(120, 240)
    payload, res = detect_and_orient_image(original, "image/png", orient_provider=None)
    assert payload == original
    assert res.angle == 0
    assert res.auto_rotated is False
    assert res.stage == "no_provider"


def test_detect_and_orient_unsupported_media_type() -> None:
    payload, res = detect_and_orient_image(b"%PDF-1.7", "application/pdf", StubOrientAdapter({}))
    assert payload == b"%PDF-1.7"
    assert res.stage == "unsupported_media_type"


def test_detect_and_orient_returns_upright_payload_unchanged() -> None:
    original = make_test_image(200, 300)
    adapter = StubOrientAdapter(
        {
            "angle": 0,
            "confidence": 0.93,
            "stage": "stage1_upright",
            "auto_rotated": False,
            "original_width": 200,
            "original_height": 300,
        }
    )

    payload, res = detect_and_orient_image(original, "image/png", orient_provider=adapter)

    assert payload == original
    assert res.angle == 0
    assert res.auto_rotated is False
    assert res.confidence == 0.93
    assert res.original_dimensions == (200, 300)
    assert adapter.calls[0]["parameters"] == {"return_image": True}


def test_detect_and_orient_applies_the_provider_image() -> None:
    original = make_test_image(200, 100)
    adapter = StubOrientAdapter(
        {
            "angle": 90,
            "confidence": 0.91,
            "stage": "stage2_evaluated",
            "auto_rotated": True,
            "original_width": 200,
            "original_height": 100,
            "oriented_width": 100,
            "oriented_height": 200,
            "image_base64": ROTATED_PNG,
        }
    )

    payload, res = detect_and_orient_image(original, "image/png", orient_provider=adapter)

    assert payload == base64.b64decode(ROTATED_PNG)
    assert payload != original
    assert res.angle == 90
    assert res.auto_rotated is True
    assert res.original_dimensions == (200, 100)
    assert res.oriented_dimensions == (100, 200)


def test_detect_and_orient_keeps_payload_when_provider_omits_the_image() -> None:
    original = make_test_image(200, 100)
    adapter = StubOrientAdapter(
        {"angle": 90, "confidence": 0.8, "stage": "stage2_evaluated", "auto_rotated": True}
    )

    payload, res = detect_and_orient_image(original, "image/png", orient_provider=adapter)

    assert payload == original
    assert res.angle == 0
    assert res.auto_rotated is False
    assert res.stage.endswith("image_unavailable")


def test_detect_and_orient_degrades_when_provider_fails() -> None:
    original = make_test_image(120, 240)

    class FailingAdapter:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("sidecar down")

    payload, res = detect_and_orient_image(original, "image/png", orient_provider=FailingAdapter())

    assert payload == original
    assert res.stage == "provider_failed"
    assert res.auto_rotated is False


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_detected_angles_are_accepted(angle: int) -> None:
    original = make_test_image(200, 100)
    adapter = StubOrientAdapter(
        {
            "angle": angle,
            "confidence": 0.9,
            "stage": "stage2_evaluated",
            "image_base64": ROTATED_PNG,
        }
    )

    _, res = detect_and_orient_image(original, "image/png", orient_provider=adapter)

    assert res.angle == angle
    assert res.auto_rotated is True


def _executor_fixture() -> tuple[CapabilityExecutor, list[bytes]]:
    capabilities = CapabilityRegistry.from_directory(Path("capabilities"))
    providers = ProviderRegistry(
        [
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
        ]
    )

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
                    "original_width": 200,
                    "original_height": 100,
                    "oriented_width": 100,
                    "oriented_height": 200,
                    "image_base64": ROTATED_PNG,
                }
            )

        def versions(self) -> dict[str, str]:
            return {"provider": "1.0"}

    def adapter_factory(desc: ProviderDescriptor) -> Any:
        if desc.id == "rapidocr-orient":
            return MockOrient()
        return MockExtractor()

    return CapabilityExecutor(capabilities, providers, adapter_factory), passed_payloads


def test_executor_passes_original_payload_without_auto_rotate() -> None:
    executor, passed_payloads = _executor_fixture()
    test_img = make_test_image(200, 100)

    executor.execute(
        "document.structure.extract",
        test_img,
        filename="test.png",
        media_type="image/png",
        parameters={"auto_rotate": False},
    )

    assert passed_payloads == [test_img]


def test_executor_rotates_payload_and_records_orientation_in_provenance() -> None:
    executor, passed_payloads = _executor_fixture()
    test_img = make_test_image(200, 100)

    result = executor.execute(
        "document.structure.extract",
        test_img,
        filename="test.png",
        media_type="image/png",
        parameters={"auto_rotate": True},
    )

    assert passed_payloads[0] == base64.b64decode(ROTATED_PNG)
    assert passed_payloads[0] != test_img
    assert result.provenance.orientation is not None
    assert result.provenance.orientation["angle"] == 90
    assert result.provenance.orientation["auto_rotated"] is True


def test_executor_ignores_auto_rotate_for_non_image_media() -> None:
    executor, passed_payloads = _executor_fixture()
    pdf = b"%PDF-1.7 payload"

    executor.execute(
        "document.structure.extract",
        pdf,
        filename="doc.pdf",
        media_type="application/pdf",
        parameters={"auto_rotate": True},
    )

    assert passed_payloads == [pdf]


@pytest.mark.parametrize("change", ["version", "model", "payload", "recovery", "config"])
def test_orientation_changes_invalidate_execution_cache(change: str) -> None:
    executor, passed_payloads = _executor_fixture()

    class Cache:
        def __init__(self):
            self.data = {}

        def get(self, key):
            return self.data.get(key)

        def put(self, key, value):
            self.data[key] = value

    executor._cache = Cache()
    original_factory = executor._adapter_factory
    state = {"version": "1", "model": "1", "image": ROTATED_PNG, "failed": change == "recovery"}

    class Orient:
        def versions(self):
            return {"provider": state["version"], "model": state["model"]}

        def execute(self, *args, **kwargs):
            if state["failed"]:
                raise RuntimeError("temporarily unavailable")
            return SimpleNamespace(
                document={
                    "angle": 90,
                    "confidence": 0.95,
                    "stage": "provider",
                    "image_base64": state["image"],
                }
            )

    executor._adapter_factory = lambda desc: (
        Orient() if desc.id == "rapidocr-orient" else original_factory(desc)
    )
    image = make_test_image(200, 100)

    def run():
        return executor.execute(
            "document.structure.extract",
            image,
            filename="test.png",
            media_type="image/png",
            parameters={"auto_rotate": True},
        )

    first = run()
    assert run().provenance.cache_hit
    assert len(passed_payloads) == 1
    if change == "recovery":
        state["failed"] = False
    elif change == "payload":
        state["image"] = UPRIGHT_PNG
    elif change == "config":
        executor._providers.get("rapidocr-orient").config["threshold"] = 0.9
    else:
        state[change] = "2"
    second = run()
    assert not second.provenance.cache_hit
    assert first.provenance.cache_key != second.provenance.cache_key
    assert len(passed_payloads) == 2
    assert second.provenance.input_fingerprints == [fingerprint_bytes(passed_payloads[-1])]
    assert run().provenance.cache_hit
    assert len(passed_payloads) == 2
