"""Builds the canonical structured document from a provider extraction.

Obligation rationales and observation messages are in English by default and
internationalized via gettext (.po) files in the project locale directory.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections import Counter
from pathlib import Path
from typing import Any

from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.normalization.latex import normalize_latex
from acessilia_toolbox.core.normalization.models import (
    BoundingBox,
    ExtractorRun,
    ManifestElement,
    ManifestSummary,
    Obligation,
    Observation,
    PageDescriptor,
    ProcessingManifest,
    Provenance,
    SourceDocument,
)
from acessilia_toolbox.core.normalization.table_ast import (
    analyze_table_complexity,
    normalize_table_ast,
    rows_from_table_ast,
)

LABEL_TO_TYPE = {
    "title": "title",
    "section_header": "heading",
    "heading": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "list_item": "list_item",
    "table": "table",
    "picture": "picture",
    "image": "picture",
    "formula": "formula",
    "equation": "formula",
    "interline_equation": "formula",
    "code": "code",
    "caption": "caption",
    "footnote": "footnote",
    "page_header": "page_header",
    "page_footer": "page_footer",
    "checkbox_selected": "checkbox",
    "checkbox_unselected": "checkbox",
    "key_value_region": "key_value",
    "form": "form",
}

# Rationale strings are manifest content; see the module docstring.
OBLIGATION_BY_TYPE = {
    "picture": (
        "describe-image",
        "The image must receive a description or be marked as decorative.",
        ["vision-description", "human-review"],
    ),
    "table": (
        "linearize-table",
        "The table must have headers and a verifiable reading order.",
        ["docling-table", "pandoc-table", "human-review"],
    ),
    "formula": (
        "verbalize-formula",
        "The formula must have an accessible mathematical representation and verbalization.",
        ["mathml", "latex-verbalizer", "human-review"],
    ),
    "code": (
        "preserve-code-semantics",
        "The code block must preserve indentation, language, and literal reading.",
        ["pandoc-code", "human-review"],
    ),
    "unknown": (
        "review-structure",
        "The unclassified element requires structural inspection.",
        ["docling-retry", "pymupdf-region", "human-review"],
    ),
}

DEFAULT_METHOD_COSTS = {
    "vision-description": 20,
    "docling-table": 10,
    "pandoc-table": 15,
    "mathml": 10,
    "latex-verbalizer": 20,
    "pandoc-code": 10,
    "docling-retry": 25,
    "pymupdf-region": 30,
    "deterministic-heading-repair": 5,
    "human-review": 100,
}

CALL_OUT_MIN_GROUP_SIZE = 3
CALL_OUT_MIN_INDENT_PX = 8.0
CALL_OUT_MIN_INDENT_RATIO = 0.015
CALL_OUT_MAX_WIDTH_RATIO = 0.9
CALL_OUT_MAX_VERTICAL_GAP = 28.0
KNOWN_CALLOUT_TITLES = {
    # English
    "note", "warning", "tip", "important", "caution",
    "info", "hint", "notice", "reminder", "attention",
    # Portuguese
    "dica", "importante", "atenção", "aviso", "nota",
    "observação", "observacao", "informação", "informacao",
    # Spanish
    "consejo", "advertencia",
    "información", "informacion", "observación", "observacion",
    "atención", "atencion", "pista", "recordatorio",
    # French
    "avertissement", "conseil",
    "information", "remarque", "astuce", "rappel",
    # German
    "hinweis", "wichtig", "achtung", "tipp", "notiz",
    "erinnerung", "warnung",
    # Italian
    "attenzione", "consiglio",
    "informazione", "osservazione", "promemoria", "avviso",
}


def build_processing_manifest(
    source_path: Path,
    extraction: ExtractionResult,
    *,
    language: str = "pt-BR",
    refine_reading_order: bool = False,
) -> ProcessingManifest:
    """Build a validated structured document from a provider extraction."""
    source_path = source_path.resolve()
    digest = _sha256(source_path)
    extractor_name = str(extraction.configuration.get("extractor", "")).strip().lower()
    enable_callouts = extractor_name != "pymupdf"
    elements = _build_elements(
        extraction.document,
        enable_callouts=enable_callouts,
        refine_reading_order=refine_reading_order,
    )
    pages = _build_pages(extraction.document, elements)
    title = _infer_title(source_path, elements)
    observations, obligations = _derive_processing_needs(elements)
    element_types = dict(sorted(Counter(e.type for e in elements).items()))

    source = SourceDocument(
        document_id=f"doc-{digest[:16]}",
        filename=source_path.name,
        path=str(source_path),
        media_type=mimetypes.guess_type(source_path.name)[0]
        or "application/octet-stream",
        byte_size=source_path.stat().st_size,
        sha256=digest,
    )

    extractor = ExtractorRun(
        name=extraction.backend,
        version=extraction.version,
        started_at=extraction.started_at,
        completed_at=extraction.completed_at,
        duration_ms=extraction.duration_ms,
        configuration=extraction.configuration,
    )

    return ProcessingManifest(
        manifest_id=f"manifest-{digest[:16]}-r1",
        created_at=extraction.completed_at,
        source=source,
        extractor=extractor,
        title=title,
        language=language,
        pages=pages,
        elements=elements,
        observations=observations,
        obligations=obligations,
        summary=ManifestSummary(
            page_count=len(pages),
            element_count=len(elements),
            observation_count=len(observations),
            obligation_count=len(obligations),
            element_types=element_types,
        ),
    )


def _build_elements(
    document: Any,
    *,
    enable_callouts: bool = True,
    refine_reading_order: bool = False,
) -> list[ManifestElement]:
    """Build the ManifestElement list from the provider document."""
    elements: list[ManifestElement] = []
    try:
        iterator = document.iterate_items(with_groups=True, traverse_pictures=True)
    except TypeError:
        iterator = document.iterate_items(with_groups=True)

    for reading_order, (item, tree_level) in enumerate(iterator, start=1):
        raw_label = _item_label(item)
        element_type = LABEL_TO_TYPE.get(raw_label, _fallback_type(item, raw_label))
        provenance = _provenance(item)
        page_number = provenance[0].page_number if provenance else None
        hierarchy_level = _hierarchy_level(item, element_type, tree_level)
        metadata = _safe_metadata(item, element_type=element_type)
        source_label = getattr(item, "raw_label", None)
        elements.append(
            ManifestElement(
                id=f"element-{reading_order:06d}",
                type=element_type,
                raw_label=(
                    source_label if isinstance(source_label, str) and source_label else raw_label
                ),
                reading_order=reading_order,
                hierarchy_level=hierarchy_level,
                text=_item_text(item, element_type),
                source_ref=_reference(getattr(item, "self_ref", None)),
                parent_ref=_reference(getattr(item, "parent", None)),
                page_number=page_number,
                confidence=_confidence(item),
                provenance=provenance,
                metadata=metadata,
            )
        )
    by_source_ref = {
        element.source_ref: element.id
        for element in elements
        if element.source_ref is not None
    }
    for element in elements:
        if element.parent_ref is not None:
            element.parent_id = by_source_ref.get(element.parent_ref)
    if enable_callouts:
        _normalize_callout_groups(elements)
    if refine_reading_order:
        elements = _refine_elements_reading_order(elements)
    return elements


def _normalize_callout_groups(elements: list[ManifestElement]) -> None:
    """Groups visually indented elements as callouts."""
    by_page: dict[int, list[ManifestElement]] = {}
    for element in elements:
        if element.page_number is not None:
            by_page.setdefault(element.page_number, []).append(element)

    for page_number in sorted(by_page):
        _normalize_page_callouts(by_page[page_number])


def _normalize_page_callouts(page_elements: list[ManifestElement]) -> None:
    _tag_known_title_callouts(page_elements)

    text_like = [
        element
        for element in page_elements
        if element.type in {"heading", "paragraph", "list_item", "group", "unknown"}
        and _element_bbox(element) is not None
        and (element.text or "").strip()
        and not element.metadata.get("callout_id")
    ]
    if len(text_like) < CALL_OUT_MIN_GROUP_SIZE:
        return

    main_left, main_right = _estimate_main_text_band(text_like)
    if main_right <= main_left:
        return

    band_width = main_right - main_left
    indent_threshold = max(CALL_OUT_MIN_INDENT_PX, band_width * CALL_OUT_MIN_INDENT_RATIO)

    candidates: list[ManifestElement] = []
    for element in sorted(text_like, key=lambda item: item.reading_order):
        bbox = _element_bbox(element)
        if bbox is None:
            continue
        left, _top, _right, _bottom = bbox
        if left >= main_left + indent_threshold:
            candidates.append(element)

    if len(candidates) < CALL_OUT_MIN_GROUP_SIZE:
        return

    groups: list[list[ManifestElement]] = []
    current_group: list[ManifestElement] = [candidates[0]]
    for candidate in candidates[1:]:
        if _is_same_callout_cluster(current_group[-1], candidate):
            current_group.append(candidate)
        else:
            if len(current_group) >= CALL_OUT_MIN_GROUP_SIZE:
                groups.append(current_group)
            current_group = [candidate]
    if len(current_group) >= CALL_OUT_MIN_GROUP_SIZE:
        groups.append(current_group)

    for group in groups:
        if any(element.metadata.get("callout_id") for element in group):
            continue
        callout_id = f"callout-auto-{group[0].reading_order:06d}"
        _apply_callout_group(group[0], group, callout_id=callout_id)


def _tag_known_title_callouts(page_elements: list[ManifestElement]) -> None:
    ordered = sorted(page_elements, key=lambda item: item.reading_order)

    for index, title in enumerate(ordered):
        if title.metadata.get("callout_id"):
            continue
        if not _is_known_callout_title(title.text or ""):
            continue
        title_bbox = _element_bbox(title)
        if title_bbox is None:
            continue

        group: list[ManifestElement] = [title]
        previous = title
        for candidate in ordered[index + 1:]:
            if candidate.metadata.get("callout_id"):
                continue
            if candidate.type in {"heading", "title"} and not _is_known_callout_title(
                candidate.text or ""
            ):
                break
            if not _is_same_callout_cluster(previous, candidate):
                break
            group.append(candidate)
            previous = candidate

        if len(group) >= 2:
            _apply_callout_group(
                title, group, callout_id=f"callout-known-{title.reading_order:06d}"
            )


def _apply_callout_group(
    title: ManifestElement,
    group: list[ManifestElement],
    *,
    callout_id: str,
) -> None:
    if title.type == "heading":
        original_type = title.type
        title.type = "paragraph"
        title.metadata["original_type"] = original_type
        title.metadata["demoted_from_heading"] = True
    title.metadata["is_callout_title"] = True
    title.metadata["callout_id"] = callout_id
    title.metadata["callout_role"] = "title"
    title.metadata["callout_type"] = "note"
    title.metadata["callout_title"] = title.text or ""

    for item in group[1:]:
        item.metadata["callout_id"] = callout_id
        item.metadata["callout_role"] = "content"
        item.metadata.setdefault("callout_type", "note")
        item.metadata.setdefault("callout_title", title.text or "")


def _estimate_main_text_band(elements: list[ManifestElement]) -> tuple[float, float]:
    wide = []
    all_boxes = []
    for element in elements:
        bbox = _element_bbox(element)
        if bbox is None:
            continue
        left, _, right, _ = bbox
        width = right - left
        all_boxes.append((left, right, width))

    if not all_boxes:
        return (0.0, 0.0)

    max_width = max(width for _, _, width in all_boxes)
    for left, right, width in all_boxes:
        if width >= max_width * 0.75:
            wide.append((left, right))

    reference = wide or [(left, right) for left, right, _ in all_boxes]
    main_left = min(left for left, _ in reference)
    main_right = max(right for _, right in reference)
    return (main_left, main_right)


def _is_same_callout_cluster(previous: ManifestElement, current: ManifestElement) -> bool:
    prev_bbox = _element_bbox(previous)
    curr_bbox = _element_bbox(current)
    if prev_bbox is None or curr_bbox is None:
        return False

    _, _prev_top, _, prev_bottom = prev_bbox
    _, curr_top, _, _ = curr_bbox
    vertical_gap = curr_top - prev_bottom
    if vertical_gap > CALL_OUT_MAX_VERTICAL_GAP:
        return False

    prev_left, _, prev_right, _ = prev_bbox
    curr_left, _, curr_right, _ = curr_bbox
    overlap_left = max(prev_left, curr_left)
    overlap_right = min(prev_right, curr_right)
    overlap_width = max(0.0, overlap_right - overlap_left)
    min_width = max(1.0, min(prev_right - prev_left, curr_right - curr_left))
    return (overlap_width / min_width) >= 0.55


def _element_bbox(element: ManifestElement) -> tuple[float, float, float, float] | None:
    if not element.provenance:
        return None
    bbox = element.provenance[0].bbox
    if bbox is None:
        return None
    return (bbox.left, bbox.top, bbox.right, bbox.bottom)


def _is_callout_title_candidate(element: ManifestElement) -> bool:
    if element.type == "heading":
        return True
    if element.type != "paragraph":
        return False
    text = (element.text or "").strip()
    if not text:
        return False
    if _is_known_callout_title(text):
        return True
    if len(text) > 95:
        return False
    upper_ratio = _uppercase_ratio(text)
    if upper_ratio >= 0.55:
        return True
    return text.endswith(("?", ":"))


def _uppercase_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    uppers = [ch for ch in letters if ch.isupper()]
    return len(uppers) / len(letters)


def _normalize_text_key(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized


def _is_known_callout_title(text: str) -> bool:
    return _normalize_text_key(text) in KNOWN_CALLOUT_TITLES


def _refine_page_reading_order(page_elements: list[ManifestElement]) -> list[ManifestElement]:
    """Refine reading order of a single page using column projection and marginal isolation.

    1. Headers (page_header) are isolated to the beginning.
    2. Footers (page_footer) are isolated to the end.
    3. Body elements are partitioned into vertical columns based on horizontal gutters.
       Within each column, elements are ordered vertically (top-to-bottom).
    """
    if len(page_elements) <= 2:
        return page_elements

    headers: list[ManifestElement] = []
    footers: list[ManifestElement] = []
    body: list[ManifestElement] = []

    for el in page_elements:
        if el.type == "page_header":
            headers.append(el)
        elif el.type == "page_footer":
            footers.append(el)
        else:
            body.append(el)

    # Sort headers and footers top-to-bottom
    def _top_key(el: ManifestElement) -> float:
        b = _element_bbox(el)
        return b[1] if b else 0.0

    headers.sort(key=_top_key)
    footers.sort(key=_top_key)

    if not body:
        return headers + footers

    # Check if multi-column partition is viable
    bboxes = [_element_bbox(el) for el in body]
    valid_elements = [el for el, b in zip(body, bboxes, strict=False) if b is not None]
    if len(valid_elements) < 3:
        # Not enough elements to reliably detect columns; sort top-to-bottom
        body.sort(key=_top_key)
        return headers + body + footers

    valid_boxes: list[tuple[float, float, float, float]] = [
        box for el in valid_elements if (box := _element_bbox(el)) is not None
    ]
    if len(valid_boxes) < 3:
        body.sort(key=_top_key)
        return headers + body + footers

    page_min_x = min(b[0] for b in valid_boxes)
    page_max_x = max(b[2] for b in valid_boxes)
    page_width = page_max_x - page_min_x

    if page_width <= 50:
        body.sort(key=_top_key)
        return headers + body + footers

    # Find elements that clearly fall into multi-column layouts
    # Check if elements cluster into distinct horizontal columns
    span_widths = [(b[2] - b[0]) for b in valid_boxes]
    median_width = sorted(span_widths)[len(span_widths) // 2]

    # Multi-column condition: median element width < 55% of page content width
    if median_width < 0.55 * page_width:
        # Group into columns by horizontal position
        # Divide into left / center / right or determine column bounds
        def _col_and_top_key(el: ManifestElement) -> tuple[int, float]:
            b = _element_bbox(el)
            if b is None:
                return (0, 0.0)
            mid_x = (b[0] + b[2]) / 2
            # 2 or 3 column partition
            if median_width < 0.35 * page_width:
                # Likely 3 columns
                col_w = page_width / 3
                col_idx = min(2, max(0, int((mid_x - page_min_x) / col_w)))
            else:
                # Likely 2 columns
                col_w = page_width / 2
                col_idx = min(1, max(0, int((mid_x - page_min_x) / col_w)))
            return (col_idx, b[1])

        body.sort(key=_col_and_top_key)
    else:
        body.sort(key=_top_key)

    return headers + body + footers


def _refine_elements_reading_order(elements: list[ManifestElement]) -> list[ManifestElement]:
    """Refine reading order across all pages."""
    by_page: dict[int, list[ManifestElement]] = {}
    no_page: list[ManifestElement] = []

    for el in elements:
        if el.page_number is not None:
            by_page.setdefault(el.page_number, []).append(el)
        else:
            no_page.append(el)

    refined: list[ManifestElement] = []
    for page_num in sorted(by_page.keys()):
        page_refined = _refine_page_reading_order(by_page[page_num])
        refined.extend(page_refined)

    refined.extend(no_page)

    # Re-assign sequential reading_order
    for order_idx, el in enumerate(refined, start=1):
        el.reading_order = order_idx

    return refined



def _build_pages(document: Any, elements: list[ManifestElement]) -> list[PageDescriptor]:
    by_page: dict[int, list[str]] = {}
    for element in elements:
        if element.page_number is not None:
            by_page.setdefault(element.page_number, []).append(element.id)

    pages: list[PageDescriptor] = []
    raw_pages = getattr(document, "pages", {}) or {}
    for page_number, page in sorted(raw_pages.items(), key=lambda pair: int(pair[0])):
        number = int(page_number)
        size = getattr(page, "size", None)
        pages.append(
            PageDescriptor(
                page_number=number,
                width=_optional_float(getattr(size, "width", None)),
                height=_optional_float(getattr(size, "height", None)),
                element_ids=by_page.get(number, []),
            )
        )

    if not pages:
        page_count = _page_count(document)
        for number in range(1, page_count + 1):
            pages.append(
                PageDescriptor(
                    page_number=number,
                    element_ids=by_page.get(number, []),
                )
            )

    return pages


def _derive_processing_needs(
    elements: list[ManifestElement],
) -> tuple[list[Observation], list[Obligation]]:
    observations: list[Observation] = []
    obligations: list[Obligation] = []

    for element in elements:
        spec = OBLIGATION_BY_TYPE.get(element.type)
        if spec is None:
            continue
        kind, rationale, methods = spec
        suffix = element.id.removeprefix("element-")
        observations.append(
            Observation(
                id=f"observation-{kind}-{suffix}",
                kind=f"{element.type}-requires-processing",
                severity="warning" if element.type != "code" else "info",
                message=rationale,
                target_ids=[element.id],
                evidence={
                    "raw_label": element.raw_label,
                    "page_number": element.page_number,
                },
            )
        )
        obligations.append(
            Obligation(
                id=f"obligation-{kind}-{suffix}",
                kind=kind,
                target_ids=[element.id],
                admissible_methods=methods,
                method_costs={
                    method: DEFAULT_METHOD_COSTS.get(method, 50)
                    for method in methods
                },
                rationale=rationale,
            )
        )

    heading_levels = [
        (element.id, element.hierarchy_level)
        for element in elements
        if element.type == "heading"
    ]
    previous = 0
    for element_id, level in heading_levels:
        if previous and level > previous + 1:
            suffix = element_id.removeprefix("element-")
            message = (
                f"Heading hierarchy skips from level {previous} to level "
                f"{level}."
            )
            observations.append(
                Observation(
                    id=f"observation-heading-gap-{suffix}",
                    kind="heading-hierarchy-gap",
                    severity="error",
                    message=message,
                    target_ids=[element_id],
                    evidence={"previous_level": previous, "current_level": level},
                )
            )
            obligations.append(
                Obligation(
                    id=f"obligation-repair-heading-{suffix}",
                    kind="repair-heading-hierarchy",
                    target_ids=[element_id],
                    admissible_methods=["deterministic-heading-repair", "human-review"],
                    method_costs={
                        "deterministic-heading-repair": DEFAULT_METHOD_COSTS[
                            "deterministic-heading-repair"
                        ],
                        "human-review": DEFAULT_METHOD_COSTS["human-review"],
                    },
                    rationale=message,
                )
            )
        previous = level
    return observations, obligations


def _item_label(item: Any) -> str:
    label = getattr(item, "label", None)
    if label is None:
        label = getattr(item, "name", None)
    value = getattr(label, "value", label)
    text = str(value or item.__class__.__name__).strip().lower()
    text = text.removeprefix("docitemlabel.").removeprefix("grouplabel.")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "unknown"


def _fallback_type(item: Any, raw_label: str) -> str:
    class_name = item.__class__.__name__.lower()
    if "group" in class_name or raw_label in {
        "list",
        "ordered_list",
        "chapter",
        "section",
        "sheet",
        "body",
        "unspecified",
    }:
        return "group"
    return "unknown"


def _item_text(item: Any, element_type: str) -> str | None:
    for attribute in ("text", "orig", "name"):
        value = getattr(item, attribute, None)
        if isinstance(value, str) and value.strip():
            if element_type == "code":
                return value.replace("\r\n", "\n").replace("\r", "\n")
            if element_type == "formula":
                norm = normalize_latex(value)
                return norm if norm else value.strip()
            cleaned = value.replace("\r\n", "\n").replace("\r", "\n")
            cleaned = re.sub(
                r"[\u0000-\u0008\u000b\u000c\u000e-\u001f]", "", cleaned
            )
            return cleaned.strip() or None
    return None


def _hierarchy_level(item: Any, element_type: str, tree_level: int) -> int:
    if element_type == "title":
        return 1
    if element_type == "heading":
        explicit = getattr(item, "level", None)
        if isinstance(explicit, int) and explicit >= 1:
            return explicit
        return max(1, int(tree_level))
    return max(0, int(tree_level))


def _provenance(item: Any) -> list[Provenance]:
    records: list[Provenance] = []
    for raw in getattr(item, "prov", None) or []:
        page_number = getattr(raw, "page_no", None)
        if not isinstance(page_number, int) or page_number < 1:
            continue
        bbox = _bbox(getattr(raw, "bbox", None))
        charspan = getattr(raw, "charspan", None)
        char_start = char_end = None
        if isinstance(charspan, (tuple, list)) and len(charspan) == 2:
            char_start, char_end = int(charspan[0]), int(charspan[1])
        records.append(
            Provenance(
                page_number=page_number,
                bbox=bbox,
                char_start=char_start,
                char_end=char_end,
            )
        )
    return records


def _bbox(raw: Any) -> BoundingBox | None:
    if raw is None:
        return None
    try:
        origin = getattr(getattr(raw, "coord_origin", None), "value", None)
        origin_text = str(origin or "UNKNOWN").upper()
        if origin_text not in {"TOPLEFT", "BOTTOMLEFT"}:
            origin_text = "UNKNOWN"
        return BoundingBox(
            left=float(raw.l),
            top=float(raw.t),
            right=float(raw.r),
            bottom=float(raw.b),
            coord_origin=origin_text,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _reference(raw: Any) -> str | None:
    if raw is None:
        return None
    value = getattr(raw, "cref", raw)
    if isinstance(value, dict):
        value = value.get("$ref") or value.get("cref")
    text = str(value).strip()
    return text or None


def _confidence(item: Any) -> float | None:
    for attribute in ("confidence", "score"):
        value = getattr(item, attribute, None)
        if isinstance(value, (int, float)) and 0 <= float(value) <= 1:
            return float(value)
    return None


def _safe_metadata(item: Any, *, element_type: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"docling_class": item.__class__.__name__}
    supplied = getattr(item, "normalization_metadata", None)
    if isinstance(supplied, dict):
        rotation = supplied.get("rotation_degrees")
        if type(rotation) is int and rotation in {0, 90, 180, 270}:
            metadata["rotation_degrees"] = rotation
        confidence_available = supplied.get("confidence_available")
        if isinstance(confidence_available, bool):
            metadata["confidence_available"] = confidence_available
    content_layer = getattr(item, "content_layer", None)
    if content_layer is not None:
        metadata["content_layer"] = str(getattr(content_layer, "value", content_layer))
    enumerated = getattr(item, "enumerated", None)
    if isinstance(enumerated, bool):
        metadata["enumerated"] = enumerated
    marker = getattr(item, "marker", None)
    if isinstance(marker, str) and marker:
        metadata["marker"] = marker

    if element_type == "table":
        table_ast = _extract_table_ast(item)
        if table_ast is not None:
            metadata["table_ast"] = table_ast
            rows = rows_from_table_ast(table_ast)
            if rows:
                metadata["table_row_count"] = len(rows)
                metadata["table_column_count"] = max((len(row) for row in rows), default=0)
            metadata["table_has_header"] = bool(table_ast.get("header"))
            complexity = analyze_table_complexity(table_ast)
            metadata["table_has_spans"] = complexity["has_spans"]
            metadata["table_max_rowspan"] = complexity["max_rowspan"]
            metadata["table_max_colspan"] = complexity["max_colspan"]
            metadata["table_is_complex"] = complexity["is_complex"]
            metadata["table_linearization_hint"] = "docling-structured"
    return metadata


def _cell_attr(cell: Any, name: str, default: Any = None) -> Any:
    if isinstance(cell, dict):
        return cell.get(name, default)
    return getattr(cell, name, default)


def _table_ast_from_docling_cells(item: Any) -> dict[str, Any] | None:
    data = getattr(item, "data", None)
    if data is None and isinstance(item, dict):
        data = item.get("data")
    if data is None:
        return None

    grid = getattr(data, "grid", None)
    table_cells = getattr(data, "table_cells", None)
    if grid is None and isinstance(data, dict):
        grid = data.get("grid")
    if table_cells is None and isinstance(data, dict):
        table_cells = data.get("table_cells")

    cells = grid or table_cells
    if not cells or not isinstance(cells, (list, tuple)):
        return None

    # Flatten nested rows if cells is 2D
    flat_cells: list[Any] = []
    for c in cells:
        if isinstance(c, (list, tuple)):
            flat_cells.extend(c)
        else:
            flat_cells.append(c)

    # Determine dimensions
    max_r = 0
    max_c = 0
    for c in flat_cells:
        r = _cell_attr(c, "start_row_offset_idx", 0) or 0
        col = _cell_attr(c, "start_col_offset_idx", 0) or 0
        r_span = _cell_attr(c, "row_span", 1) or 1
        c_span = _cell_attr(c, "col_span", 1) or 1
        max_r = max(max_r, r + r_span)
        max_c = max(max_c, col + c_span)

    if max_r == 0 or max_c == 0:
        return None

    grid_matrix: list[list[dict[str, Any] | None]] = [
        [None for _ in range(max_c)] for _ in range(max_r)
    ]
    for c in flat_cells:
        r = _cell_attr(c, "start_row_offset_idx", 0) or 0
        col = _cell_attr(c, "start_col_offset_idx", 0) or 0
        text = str(_cell_attr(c, "text", "") or "")
        is_header = bool(_cell_attr(c, "column_header", False))
        cell_dict: dict[str, Any] = {"text": text}
        if is_header:
            cell_dict["scope"] = "col"
        r_span = _cell_attr(c, "row_span", 1) or 1
        c_span = _cell_attr(c, "col_span", 1) or 1
        if r_span > 1:
            cell_dict["row_span"] = r_span
        if c_span > 1:
            cell_dict["col_span"] = c_span
        if 0 <= r < max_r and 0 <= col < max_c:
            grid_matrix[r][col] = cell_dict

    header_rows: list[dict[str, Any]] = []
    body_rows: list[dict[str, Any]] = []

    for r in range(max_r):
        row_cells: list[dict[str, Any]] = []
        for col in range(max_c):
            cell = grid_matrix[r][col]
            if cell is not None:
                row_cells.append(cell)
            else:
                row_cells.append({"text": ""})

        row_is_header = any(c.get("scope") == "col" for c in row_cells)
        if row_is_header and not body_rows:
            header_rows.append({"cells": row_cells})
        else:
            body_rows.append({"cells": row_cells})

    return {"header": header_rows, "body": body_rows}


def _extract_table_ast(item: Any) -> dict[str, Any] | None:
    docling_ast = _table_ast_from_docling_cells(item)
    if docling_ast is not None:
        normalized = normalize_table_ast(docling_ast)
        if normalized is not None:
            return normalized

    raw_candidates: list[Any] = []
    for attr_name in (
        "table_ast",
        "table",
        "table_data",
        "grid",
        "rows",
        "cells",
    ):
        value = getattr(item, attr_name, None)
        if value is not None:
            raw_candidates.append(value)

    for method_name in ("model_dump", "to_dict", "export_to_dict"):
        method = getattr(item, method_name, None)
        if not callable(method):
            continue
        try:
            payload = method()
        except TypeError:
            try:
                payload = method(mode="json")
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(payload, dict):
            raw_candidates.append(payload)
            for nested_key in (
                "table_ast",
                "table",
                "table_data",
                "grid",
                "rows",
                "cells",
            ):
                nested = payload.get(nested_key)
                if nested is not None:
                    raw_candidates.append(nested)

    for candidate in raw_candidates:
        table_ast = normalize_table_ast(candidate)
        if table_ast is not None:
            return table_ast
    return None


def _infer_title(source_path: Path, elements: list[ManifestElement]) -> str:
    titles = [
        (element.text or "").strip()
        for element in elements
        if element.type == "title" and (element.text or "").strip()
    ]
    if titles:
        return titles[0]

    headings = [
        (element.text or "").strip()
        for element in elements
        if element.type == "heading" and (element.text or "").strip()
    ]
    if len(headings) >= 2 and _looks_like_chapter_heading(headings[0]):
        return f"{headings[0]} - {headings[1]}"
    if headings:
        return headings[0]
    return source_path.stem


def _looks_like_chapter_heading(text: str) -> bool:
    normalized = text.strip().upper()
    return bool(re.match(
        r"^(?:"
        r"CAP[ÍI]TULO|CAPITULO|CHAPTER|CHAPITRE|"
        r"KAPITEL|CAPÍTULO|CAPITOLO"
        r")\s+\d+",
        normalized,
    ))


def _page_count(document: Any) -> int:
    num_pages = getattr(document, "num_pages", None)
    if callable(num_pages):
        try:
            return max(0, int(num_pages()))
        except (TypeError, ValueError):
            pass
    return 0


def _optional_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
