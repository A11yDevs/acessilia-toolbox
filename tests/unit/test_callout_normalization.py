"""Indented groups the normalizer reads as callouts, and the ones it leaves alone."""

from __future__ import annotations

from acessilia_toolbox.core.normalization.builder import _normalize_callout_groups
from acessilia_toolbox.core.normalization.models import (
    BoundingBox,
    ManifestElement,
    Provenance,
)

LONG = "Body text that runs across the whole column of the page, as prose does. " * 2


def _element(
    order: int,
    text: str,
    left: float,
    top: float,
    *,
    kind: str = "paragraph",
    right: float = 532.0,
    **metadata: object,
) -> ManifestElement:
    return ManifestElement(
        id=f"element-{order:06d}",
        type=kind,
        raw_label="list_item" if kind == "list_item" else "text",
        reading_order=order,
        hierarchy_level=0,
        text=text,
        page_number=1,
        provenance=[
            Provenance(
                page_number=1,
                bbox=BoundingBox(
                    left=left, top=top, right=right, bottom=top + 12, coord_origin="TOPLEFT"
                ),
            )
        ],
        metadata=dict(metadata),
    )


def _callout_ids(elements: list[ManifestElement]) -> list[str | None]:
    return [element.metadata.get("callout_id") for element in elements]


def test_an_indented_numbered_list_is_not_a_callout() -> None:
    """The calculus handout: an intro line, then items 1. to 4. with a continuation."""
    elements = [
        _element(1, LONG, 65, 100),
        _element(2, LONG, 65, 120),
        _element(3, "We close with a few remarks:", 82, 140, right=345),
        _element(4, "The method lets us handle many functions.", 79, 160,
                 kind="list_item", enumerated=True, marker="1."),
        _element(5, "to find out what happens to the quotient.", 94, 175, right=524),
        _element(6, "The expression is undefined when h is zero.", 79, 190,
                 kind="list_item", enumerated=True, marker="2."),
        _element(7, "These ideas come back next week.", 79, 205,
                 kind="list_item", enumerated=True, marker="3."),
        _element(8, "It all rests on the average velocity.", 79, 220,
                 kind="list_item", enumerated=True, marker="4."),
    ]

    _normalize_callout_groups(elements)

    assert _callout_ids(elements) == [None] * len(elements)


def test_a_numbered_item_does_not_title_the_text_after_it() -> None:
    elements = [
        _element(1, LONG, 65, 100),
        _element(2, LONG, 65, 120),
        _element(3, "Check that, after simplifying, we have", 79, 140,
                 kind="list_item", enumerated=True, marker="2."),
        _element(4, "Letting h approach zero, find v(3).", 79, 155,
                 kind="list_item", enumerated=True, marker="3."),
        _element(5, "Repeating the argument above, check that", 82, 170, right=358),
    ]

    _normalize_callout_groups(elements)

    assert _callout_ids(elements) == [None] * len(elements)


def test_an_indented_box_with_a_title_is_still_a_callout() -> None:
    elements = [
        _element(1, LONG, 65, 100),
        _element(2, LONG, 65, 120),
        _element(3, "DO WE REALLY NEED ANOTHER CLASS?", 90, 140, right=500),
        _element(4, "We could have made the class more general.", 90, 155, right=500),
        _element(5, "That works, but it grows hard to maintain.", 90, 170, right=500),
    ]

    _normalize_callout_groups(elements)

    assert _callout_ids(elements) == [None, None] + ["callout-auto-000003"] * 3
    assert elements[2].metadata["callout_role"] == "title"
    assert elements[3].metadata["callout_role"] == "content"


def test_an_indented_group_without_a_title_is_not_a_callout() -> None:
    elements = [
        _element(1, LONG, 65, 100),
        _element(2, LONG, 65, 120),
        _element(3, "An indented sentence that is plain running text and no title.", 90, 140,
                 right=500),
        _element(4, "Another indented sentence.", 90, 155, right=500),
        _element(5, "And a third one.", 90, 170, right=500),
    ]

    _normalize_callout_groups(elements)

    assert _callout_ids(elements) == [None] * len(elements)


def test_a_list_inside_a_titled_box_stays_in_the_callout() -> None:
    elements = [
        _element(1, LONG, 65, 100),
        _element(2, LONG, 65, 120),
        _element(3, "BEFORE YOU START", 90, 140, right=500),
        _element(4, "Make sure the tools are installed.", 90, 155, right=500),
        _element(5, "A compiler.", 90, 170, right=500, kind="list_item"),
        _element(6, "A text editor.", 90, 185, right=500, kind="list_item"),
        _element(7, "Then open the first exercise.", 90, 200, right=500),
    ]

    _normalize_callout_groups(elements)

    assert _callout_ids(elements) == [None, None] + ["callout-auto-000003"] * 5
