"""What each schema reference in the capability manifests points at.

A manifest names its input and output by a reference such as
``artifact/structured-document@1``. Most resolve to a JSON Schema published in
``schemas/<name>@<major>.json``. The others are listed here, each with the
reason it has no file, so that the published contract can say so and a client
generating bindings (A11yDevs/acessilia#213) never has to guess from a missing
file or an internal test:

- ``descriptor``: the reference describes how the data travels, not a JSON
  document: raw bytes, an uploaded file, request parameters.
- ``external``: a JSON format defined outside the Toolbox.
- ``unpublished``: a JSON document the Toolbox produces or reads whose schema
  is not written yet. These entries should only disappear, as schemas land.

``tests/unit/test_capability_registry.py`` checks that every reference, input
and output, is either a published file or listed here exactly once, and that
nothing listed here has a file or is unused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

SchemaStatus = Literal["published", "descriptor", "external", "unpublished"]

DESCRIPTORS: dict[str, str] = {
    "artifact/binary@1": "raw bytes; the media types of the capability describe them",
    "artifact/document@1": "an uploaded document file, of one of the input media types",
    "artifact/query@1": "request parameters; the call has no document body",
}

EXTERNAL: dict[str, str] = {
    "artifact/pandoc-ast@1": "Pandoc's JSON AST (pandoc-types); the format is Pandoc's",
}

UNPUBLISHED: dict[str, str] = {
    "artifact/dataset-item-list@1": "JSON returned by the dataset capabilities; no schema yet",
    "artifact/dataset-list@1": "JSON returned by the dataset capabilities; no schema yet",
    "artifact/dataset-splits@1": "JSON returned by the dataset capabilities; no schema yet",
    "artifact/dataset-sync-report@1": "JSON returned by the dataset capabilities; no schema yet",
    "artifact/layout-analysis@1": "JSON returned by document.layout.analyze; no schema yet",
}

_LISTED: dict[str, tuple[SchemaStatus, str]] = {
    **{ref: ("descriptor", note) for ref, note in DESCRIPTORS.items()},
    **{ref: ("external", note) for ref, note in EXTERNAL.items()},
    **{ref: ("unpublished", note) for ref, note in UNPUBLISHED.items()},
}


def schema_filename(ref: str) -> str:
    """``artifact/pdf-export@1`` -> ``pdf-export@1.json``."""
    name, _, major = ref.partition("@")
    return f"{name.rsplit('/', 1)[-1]}@{major}.json"


def classify(ref: str, schemas_dir: Path) -> dict[str, str | None]:
    """The contract entry for ``ref``: its status, and its file or the reason it has none.

    A reference with a file is ``published``. A reference with neither a file
    nor an entry above raises ``ValueError``: it must be classified before it
    is published.
    """
    filename = schema_filename(ref)
    if (schemas_dir / filename).is_file():
        return {"ref": ref, "status": "published", "file": f"schemas/{filename}", "note": None}
    if ref not in _LISTED:
        raise ValueError(
            f"schema reference {ref!r} has no file in schemas/ and is not classified "
            "in acessilia_toolbox.core.schema_refs"
        )
    status, note = _LISTED[ref]
    return {"ref": ref, "status": status, "file": None, "note": note}


def listed() -> dict[str, SchemaStatus]:
    """Every reference this module classifies, with its status."""
    return {ref: status for ref, (status, _) in _LISTED.items()}
