"""Fake provider documents used by the normalization tests.

They mimic the shape the builder consumes so the suite stays free of
providers, containers and ML models.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult

TIMESTAMP = datetime(2026, 7, 27, tzinfo=UTC)


def bbox(left: float, top: float, right: float, bottom: float) -> SimpleNamespace:
    return SimpleNamespace(
        l=left,
        t=top,
        r=right,
        b=bottom,
        coord_origin=SimpleNamespace(value="TOPLEFT"),
    )


def provenance(
    page: int = 1,
    box: SimpleNamespace | None = None,
    charspan: tuple[int, int] | None = (0, 6),
) -> SimpleNamespace:
    return SimpleNamespace(
        page_no=page,
        bbox=box if box is not None else bbox(10, 20, 200, 80),
        charspan=charspan,
    )


def body_root() -> tuple[SimpleNamespace, int]:
    return (
        SimpleNamespace(label=None, name="body", self_ref="#/body", parent=None),
        0,
    )


def item(label: str, self_ref: str, **fields: Any) -> tuple[SimpleNamespace, int]:
    return (
        SimpleNamespace(
            label=SimpleNamespace(value=label),
            self_ref=self_ref,
            parent=SimpleNamespace(cref="#/body"),
            content_layer=SimpleNamespace(value="body"),
            **fields,
        ),
        1,
    )


class FakeDocument:
    """Minimal stand-in for a provider document."""

    def __init__(
        self,
        items: list[tuple[SimpleNamespace, int]] | None = None,
        *,
        width: float = 595,
        height: float = 842,
    ) -> None:
        self.items = items if items is not None else _default_items()
        self.pages = {1: SimpleNamespace(size=SimpleNamespace(width=width, height=height))}

    def iterate_items(self, **_: object) -> Any:
        return iter(self.items)

    def num_pages(self) -> int:
        return 1


def _default_items() -> list[tuple[SimpleNamespace, int]]:
    return [
        body_root(),
        item(
            "title",
            "#/texts/0",
            text="Test document",
            level=1,
            prov=[provenance()],
        ),
        item("picture", "#/pictures/0", text="", prov=[provenance()]),
    ]


def extraction_of(document: FakeDocument) -> ExtractionResult:
    return ExtractionResult(
        document=document,
        backend="docling",
        started_at=TIMESTAMP,
        completed_at=TIMESTAMP,
        duration_ms=5,
        version="2.test",
        configuration={"ocr": False},
    )


def sample_pdf(tmp_path: Path) -> Path:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-test")
    return source
