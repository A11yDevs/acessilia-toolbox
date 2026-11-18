"""The document part of artifact/structured-document@1 agrees with docstruct.

docstruct (``docstruct.manifest``) is the normative source of the document's
types and invariants; the published ``processing-manifest:1.1.0`` schema is
not generated from it but checked against it here (acessilia#203, D1):

- every document type in the schema's ``$defs``, and the document fields of
  the manifest itself, have exactly the fields, types, nullability, defaults,
  bounds and enums that ``docstruct.manifest.field_specs`` declares;
- the recorded snapshots convert to docstruct and back without loss;
- the model rejects every document docstruct finds invalid, for the same
  reason, and keeps every document docstruct keeps. The cases are docstruct's
  shared catalog (``docstruct.manifest_cases``, 0.5.5), which the core and the
  SDK run too (acessilia#470, criterion 4);
- the process invariants of 1.1.0 that are not docstruct's.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from docstruct.manifest import (
    MSG_FIELD_NOT_ALLOWED,
    MSG_FIELD_OUT_OF_RANGE,
    MSG_FIELD_WRONG_TYPE,
    WIRE_NAMES,
    StructuredDocument,
    field_specs,
    structured_document_from_payload,
    structured_document_to_payload,
    validate_structured_document,
)
from docstruct.manifest_cases import (
    BROKEN,
    SUPPORTED,
    document_findings,
    example_document,
)
from pydantic import ValidationError

from acessilia_toolbox.core.normalization.models import ProcessingManifest
from acessilia_toolbox.core.normalization.schema import processing_manifest_schema

SNAPSHOTS = sorted(
    (Path(__file__).resolve().parents[1] / "snapshot" / "snapshots").glob("*.json")
)
DOCUMENT_FIELDS = ("title", "language", "pages", "elements")
BOUNDS = ("minimum", "exclusiveMinimum", "maximum", "minLength")
SCHEMA = processing_manifest_schema()


def _type_spec(schema: dict) -> dict:
    options = schema.get("anyOf", [schema])
    concrete = [option for option in options if option != {"type": "null"}]
    assert len(concrete) == 1, schema
    (base,) = concrete
    if "$ref" in base:
        spec = {"type": "ref", "ref": base["$ref"].rsplit("/", 1)[-1]}
    else:
        spec = {"type": base["type"]}
        if base["type"] == "array":
            spec["items"] = _type_spec(base["items"])
        if "enum" in base:
            spec["enum"] = base["enum"]
        spec.update({key: base[key] for key in BOUNDS if key in base})
    spec["nullable"] = len(concrete) < len(options)
    return spec


def _wire_specs(properties: dict, required: list[str]) -> dict[str, dict]:
    specs = {}
    for name, schema in properties.items():
        spec = _type_spec(schema)
        spec["required"] = name in required
        if "default" in schema:
            spec["default"] = schema["default"]
        specs[name] = spec
    return specs


@pytest.mark.parametrize("docstruct_type", list(WIRE_NAMES), ids=lambda cls: cls.__name__)
def test_document_type_in_the_schema_matches_docstruct(docstruct_type) -> None:
    definition = SCHEMA["$defs"][WIRE_NAMES[docstruct_type]]

    assert definition["additionalProperties"] is False
    assert _wire_specs(definition["properties"], definition["required"]) == field_specs(
        docstruct_type
    )


def test_document_fields_of_the_manifest_match_docstruct() -> None:
    properties = {name: SCHEMA["properties"][name] for name in DOCUMENT_FIELDS}
    required = [name for name in SCHEMA["required"] if name in DOCUMENT_FIELDS]

    assert _wire_specs(properties, required) == field_specs(StructuredDocument)


def _snapshot(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))["document"]


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda path: path.stem)
def test_snapshot_document_round_trips_through_docstruct(snapshot: Path) -> None:
    payload = _snapshot(snapshot)

    document = structured_document_from_payload(payload)

    assert structured_document_to_payload(document) == {
        name: payload[name] for name in DOCUMENT_FIELDS
    }
    assert validate_structured_document(document) == []
    ProcessingManifest.model_validate(payload)


def _valid() -> dict:
    payload = copy.deepcopy(_snapshot(SNAPSHOTS[0]))
    elements = payload["elements"]
    assert len(elements) >= 2 and len(payload["pages"]) >= 1
    assert elements[0]["provenance"]
    return payload


def _with_summary(payload: dict) -> dict:
    """The summary follows the document: a shared case changes the document
    only, so the counts are recomputed after it."""
    summary = payload["summary"]
    summary["page_count"] = len(payload["pages"])
    summary["element_count"] = len(payload["elements"])
    types: dict[str, int] = {}
    for element in payload["elements"]:
        types[element["type"]] = types.get(element["type"], 0) + 1
    summary["element_types"] = dict(sorted(types.items()))
    return payload


def _example() -> dict:
    """docstruct's shared example document, in a snapshot's process fields.

    The snapshot's observations and obligations target its own elements, so
    the example goes without them."""
    payload = {**_valid(), **example_document(), "observations": [], "obligations": []}
    payload["summary"].update(observation_count=0, obligation_count=0)
    return _with_summary(payload)


def _fits_the_catalog(payload: dict) -> bool:
    """What the shared cases need: two elements, the first listed on a page
    and holding a provenance entry."""
    elements = payload["elements"]
    return (
        len(elements) >= 2
        and any(elements[0]["id"] in page["element_ids"] for page in payload["pages"])
        and bool(elements[0]["provenance"])
    )


def _from_snapshot(path: Path):
    return lambda: copy.deepcopy(_snapshot(path))


#: The documents the shared cases are applied to: every recorded snapshot the
#: catalog fits (review of #164), and docstruct's example.
BASES = {
    **{
        f"snapshot {path.stem}": _from_snapshot(path)
        for path in SNAPSHOTS
        if _fits_the_catalog(_snapshot(path))
    },
    "docstruct example": _example,
}


def test_the_catalog_runs_on_the_hierarchical_snapshots() -> None:
    """001 and 008 (55 elements, a deep hierarchy) must stay bases."""
    assert {"snapshot 001", "snapshot 008"} <= set(BASES)


def _at(payload, location: tuple) -> bool:
    """Whether ``location`` exists in ``payload``."""
    value = payload
    for key in location:
        try:
            value = value[key]
        except (KeyError, IndexError, TypeError):
            return False
    return True
FIELD_MESSAGES = (MSG_FIELD_WRONG_TYPE, MSG_FIELD_OUT_OF_RANGE, MSG_FIELD_NOT_ALLOWED)


def _location(path: str) -> tuple:
    """``elements[0].provenance[0].bbox`` as a Pydantic error location."""
    return tuple(
        int(part) if part.isdigit() else part for part in re.findall(r"[^.\[\]]+", path)
    )


def test_the_shared_example_is_a_valid_manifest() -> None:
    ProcessingManifest.model_validate(_example())


@pytest.mark.parametrize("base", list(BASES))
@pytest.mark.parametrize("case", list(BROKEN))
def test_document_invariants_are_enforced(case: str, base: str) -> None:
    """Rejected for docstruct's reason: a field finding at the same location,
    the unknown field the case added at its location, and an invariant with
    docstruct's message, which the model raises for its first finding."""
    payload = BASES[base]()
    before = copy.deepcopy(payload)
    BROKEN[case](payload)
    _with_summary(payload)
    findings = document_findings(payload)
    assert findings != []
    message, context = findings[0]

    with pytest.raises(ValidationError) as raised:
        ProcessingManifest.model_validate(payload)

    errors = raised.value.errors()
    if message == "refused":
        # The field the case added, at its location (review of #164).
        added = [
            tuple(error["loc"])
            for error in errors
            if error["type"] == "extra_forbidden"
            and _at(payload, tuple(error["loc"]))
            and not _at(before, tuple(error["loc"]))
        ]
        assert added, errors
    elif message in FIELD_MESSAGES:
        assert _location(context["path"]) in [tuple(error["loc"]) for error in errors], errors
    else:
        assert message.format(**context) in str(raised.value)


@pytest.mark.parametrize("base", list(BASES))
@pytest.mark.parametrize("case", list(SUPPORTED))
def test_supported_document_shapes_stay_valid(case: str, base: str) -> None:
    payload = BASES[base]()
    SUPPORTED[case](payload)
    _with_summary(payload)

    assert document_findings(payload) == []
    ProcessingManifest.model_validate(payload)


def _observation(payload: dict, observation_id: str) -> dict:
    return {
        "id": observation_id,
        "kind": "check",
        "severity": "info",
        "message": "check",
        "target_ids": [payload["elements"][0]["id"]],
    }


def _duplicate_observation_ids(payload: dict) -> None:
    payload["observations"] = [_observation(payload, "o1"), _observation(payload, "o1")]
    payload["summary"]["observation_count"] = 2


def _obligation(payload: dict, **changes) -> dict:
    obligation = {
        "id": "ob1",
        "kind": "describe-image",
        "target_ids": [payload["elements"][0]["id"]],
        "admissible_methods": ["vision"],
        "method_costs": {"vision": 1},
        "rationale": "test",
    }
    obligation.update(changes)
    return obligation


def _with_obligations(*obligations):
    def change(payload: dict) -> None:
        payload["obligations"] = [_obligation(payload, **o) for o in obligations]
        payload["summary"]["obligation_count"] = len(obligations)

    return change


def _duplicate_artifact_ids(payload: dict) -> None:
    artifact = {"id": "a1", "kind": "k", "path": "p", "media_type": "text/plain"}
    payload["artifacts"] = [artifact, dict(artifact)]


def _inverted_extraction(payload: dict) -> None:
    started = datetime.fromisoformat(payload["extractor"]["started_at"])
    payload["extractor"]["completed_at"] = (started - timedelta(seconds=1)).isoformat()



def _mixed_timezones(payload: dict) -> None:
    started = datetime.fromisoformat(payload["extractor"]["started_at"])
    completed = started + timedelta(seconds=1)
    # Flip the awareness of completed_at relative to started_at.
    completed = (
        completed.astimezone() if started.tzinfo is None else completed.replace(tzinfo=None)
    )
    payload["extractor"]["completed_at"] = completed.isoformat()


PROCESS_BREAKS = {
    "duplicate observation ids": _duplicate_observation_ids,
    "duplicate obligation ids": _with_obligations({}, {}),
    "duplicate artifact ids": _duplicate_artifact_ids,
    "extraction ends before it starts": _inverted_extraction,
    "extraction timestamps in different timezones": _mixed_timezones,
    "attempt timestamps in different timezones": _with_obligations(
        {
            "attempts": [
                {
                    "method": "vision",
                    "status": "failed",
                    "started_at": "2026-10-20T00:00:00",
                    "completed_at": "2026-10-20T00:00:01+00:00",
                }
            ]
        }
    ),
    "negative method cost": _with_obligations({"method_costs": {"vision": -1}}),
    "cost for a method that is not admissible": _with_obligations(
        {"admissible_methods": [], "method_costs": {"vision": 1}}
    ),
}


@pytest.mark.parametrize("case", list(PROCESS_BREAKS))
def test_process_invariants_of_1_1_0_are_enforced(case: str) -> None:
    payload = _valid()
    PROCESS_BREAKS[case](payload)

    with pytest.raises(ValidationError):
        ProcessingManifest.model_validate(payload)


def test_a_valid_obligation_still_validates() -> None:
    payload = _valid()
    _with_obligations({})(payload)

    ProcessingManifest.model_validate(payload)
