"""Sidecar orientation logic: health contract and angle detection."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

SERVER_PATH = (
    Path(__file__).resolve().parents[2] / "docker" / "rapidocr-orient" / "server.py"
)


@pytest.fixture(scope="module")
def server() -> Any:
    spec = importlib.util.spec_from_file_location("rapidocr_orient_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _line_box(width: int, height: int) -> list[list[float]]:
    return [[0.0, 0.0], [float(width), 0.0], [float(width), float(height)], [0.0, float(height)]]


def _crop(width: int = 200, height: int = 20) -> Any:
    import numpy as np

    return np.zeros((height, width, 3), dtype=np.uint8)


class StubClassifier:
    """Mirrors the RapidOCR angle classifier surface used by the server."""

    cls_thresh = 0.9

    def __init__(self, label: str, score: float) -> None:
        self.label = label
        self.score = score
        self.calls = 0

    def __call__(self, crops: list[Any]) -> tuple[list[Any], list[list[Any]], float]:
        self.calls += 1
        return crops, [[self.label, self.score] for _ in crops], 0.0


class StubEngine:
    """Returns caller-supplied results for the rotation currently applied."""

    def __init__(
        self,
        by_angle: dict[int, list[Any]] | None = None,
        *,
        classifier: StubClassifier | None = None,
    ) -> None:
        self.by_angle = by_angle or {}
        self.seen_angles: list[int] = []
        self.text_cls = classifier
        self.use_cls = classifier is not None

    def __call__(self, image: Any, **kwargs: Any) -> tuple[list[Any], list[float]]:
        # The server hands the engine a numpy array, so the stub encodes the
        # rotation applied so far in the pixels to tell the passes apart.
        clockwise = int(image.flat[0]) * 90
        self.seen_angles.append(clockwise)
        return self.by_angle.get(clockwise, []), []


class StubImage:
    """Minimal stand-in for the PIL image surface the server uses.

    `clockwise` tracks the rotation applied so far, so the engine can return
    different OCR results per candidate orientation.
    """

    def __init__(self, clockwise: int = 0, size: tuple[int, int] = (1024, 1024)) -> None:
        self.clockwise = clockwise % 360
        self.size = size

    def convert(self, mode: str) -> StubImage:
        return self

    def resize(self, size: tuple[int, int], resample: Any = None) -> StubImage:
        return StubImage(self.clockwise, size)

    def rotate(self, degrees: float, expand: bool = False) -> StubImage:
        # The server passes PIL's counter-clockwise angle; convert back.
        return StubImage(self.clockwise + (360 - int(degrees)) % 360, self.size)

    def __array__(self, dtype: Any = None, copy: Any = None) -> Any:
        import numpy as np

        # One byte per quarter turn: enough to identify the candidate angle.
        return np.full((2, 2, 3), self.clockwise // 90, dtype=np.uint8)


def _horizontal_lines(count: int = 4, score: float = 0.95) -> list[Any]:
    return [[_line_box(200, 20), _crop(200, 20), score] for _ in range(count)]


def _vertical_lines(count: int = 4, score: float = 0.6) -> list[Any]:
    return [[_line_box(20, 200), _crop(20, 200), score] for _ in range(count)]


def test_health_returns_503_until_the_engine_is_ready(server: Any) -> None:
    server.state["engine_ready"] = False
    with pytest.raises(HTTPException) as excinfo:
        server.health()
    assert excinfo.value.status_code == 503
    assert "not ready" in excinfo.value.detail


def test_health_reports_ok_when_the_engine_is_ready(server: Any) -> None:
    server.state["engine_ready"] = True
    try:
        assert server.health() == {"status": "ok", "engine_ready": True}
    finally:
        server.state["engine_ready"] = False


def test_missing_engine_is_reported_explicitly(server: Any) -> None:
    assert server._detect_orientation(None, StubImage()) == (0, 0.0, "engine_unavailable")


def test_empty_page_reports_no_text(server: Any) -> None:
    engine = StubEngine({})
    assert server._detect_orientation(engine, StubImage()) == (0, 0.0, "no_text_detected")


def test_upright_page_is_detected_without_rotating(server: Any) -> None:
    engine = StubEngine({0: _horizontal_lines(), 90: _vertical_lines(), 270: _vertical_lines()})

    angle, _, stage = server._detect_orientation(engine, StubImage())

    assert angle == 0
    assert stage == "stage1_upright"


@pytest.mark.parametrize("angle", [90, 270])
def test_quarter_turned_pages_are_detected(server: Any, angle: int) -> None:
    """The rotated page reads better after a quarter turn, so it wins."""
    engine = StubEngine(
        {
            0: _vertical_lines(score=0.55),
            90: _horizontal_lines(score=0.95) if angle == 90 else _vertical_lines(score=0.55),
            270: _horizontal_lines(score=0.95) if angle == 270 else _vertical_lines(score=0.55),
        }
    )

    detected, _, stage = server._detect_orientation(engine, StubImage())

    assert detected == angle
    assert stage == "stage2_evaluated"


def test_180_is_reachable_when_the_classifier_votes_upside_down(server: Any) -> None:
    """A 180° page keeps horizontal text; only the classifier can reveal it."""
    classifier = StubClassifier("180", 0.99)
    engine = StubEngine({0: _horizontal_lines()}, classifier=classifier)

    detected, _, stage = server._detect_orientation(engine, StubImage())

    assert detected == 180
    assert stage == "stage1_classifier_180"
    assert classifier.calls == 1


def test_classifier_label_0_leaves_the_page_upright(server: Any) -> None:
    classifier = StubClassifier("0", 0.99)
    engine = StubEngine(
        {0: _horizontal_lines(), 90: _vertical_lines(), 270: _vertical_lines()},
        classifier=classifier,
    )

    detected, _, _ = server._detect_orientation(engine, StubImage())

    assert detected == 0


def test_low_confidence_classifier_vote_is_ignored(server: Any) -> None:
    """A vote below the classifier threshold must not rotate the page."""
    classifier = StubClassifier("180", 0.2)
    engine = StubEngine(
        {0: _horizontal_lines(), 90: _vertical_lines(), 270: _vertical_lines()},
        classifier=classifier,
    )

    detected, _, _ = server._detect_orientation(engine, StubImage())

    assert detected == 0


def test_classifier_failure_does_not_break_detection(server: Any) -> None:
    class ExplodingClassifier:
        cls_thresh = 0.9

        def __call__(self, crops: list[Any]) -> Any:
            raise RuntimeError("onnx session closed")

    engine = StubEngine(
        {0: _horizontal_lines(), 90: _vertical_lines(), 270: _vertical_lines()},
        classifier=ExplodingClassifier(),  # type: ignore[arg-type]
    )

    detected, _, _ = server._detect_orientation(engine, StubImage())

    assert detected == 0


def test_missing_classifier_falls_back_to_geometry(server: Any) -> None:
    engine = StubEngine(
        {0: _horizontal_lines(), 90: _vertical_lines(score=0.4), 270: _vertical_lines(score=0.4)}
    )

    detected, _, _ = server._detect_orientation(engine, StubImage())

    assert detected == 0


def test_text_line_score_ignores_vertical_boxes(server: Any) -> None:
    assert server._text_line_score(_vertical_lines(score=0.99)) == 0.0
    assert server._text_line_score(_horizontal_lines(score=0.5)) == 0.5
    assert server._text_line_score([]) == 0.0
