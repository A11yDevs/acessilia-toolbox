"""The document part of artifact/structured-document@1 agrees with docstruct.

docstruct (``docstruct.manifest``) is the normative source of the document's
types and invariants; the published ``processing-manifest:1.1.0`` schema is
not generated from it but checked against it here (acessilia#203, D1):

- every document type in the schema's ``$defs``, and the document fields of
  the manifest itself, have exactly the fields, types, nullability, defaults,
  bounds and enums that ``docstruct.manifest.field_specs`` declares;
- the recorded snapshots convert to docstruct and back without loss;
- the model rejects every document docstruct finds invalid, and the process
  invariants of 1.1.0 that are not docstruct's.
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest
from docstruct.manifest import (
    WIRE_NAMES,
    StructuredDocument,
    field_specs,
    structured_document_from_payload,
    structured_document_to_payload,
    validate_structured_document,
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
    return payload


def _element_parent_cycle(payload: dict) -> None:
    first, second = payload["elements"][0], payload["elements"][1]
    first["parent_id"], second["parent_id"] = second["id"], first["id"]


def _duplicate_element_id(payload: dict) -> None:
    payload["elements"].append(copy.deepcopy(payload["elements"][0]))
    payload["summary"]["element_count"] += 1


DOCUMENT_BREAKS = {
    "duplicate element id": _duplicate_element_id,
    "missing parent": lambda p: p["elements"][0].update(parent_id="nowhere"),
    "own parent": lambda p: p["elements"][0].update(parent_id=p["elements"][0]["id"]),
    "parent cycle": _element_parent_cycle,
    "page lists an unknown element": lambda p: p["pages"][0]["element_ids"].append("ghost"),
    "character range ends before it starts": lambda p: p["elements"][0]["provenance"][0].update(
        char_start=5, char_end=2
    ),
}


@pytest.mark.parametrize("case", list(DOCUMENT_BREAKS))
def test_document_invariants_are_enforced(case: str) -> None:
    payload = _valid()
    DOCUMENT_BREAKS[case](payload)

    assert validate_structured_document(structured_document_from_payload(payload)) != []
    with pytest.raises(ValidationError):
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
    from datetime import datetime

    started = datetime.fromisoformat(payload["extractor"]["started_at"])
    payload["extractor"]["completed_at"] = (started - timedelta(seconds=1)).isoformat()


PROCESS_BREAKS = {
    "duplicate observation ids": _duplicate_observation_ids,
    "duplicate obligation ids": _with_obligations({}, {}),
    "duplicate artifact ids": _duplicate_artifact_ids,
    "extraction ends before it starts": _inverted_extraction,
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
