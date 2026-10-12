"""Byte-level and structural integrity of the published JSON schemas.

A schema that ships with a byte-order mark, a stray control character, or a
hand-edited key without quotes is not loadable by any consumer. These checks
run over every file in ``schemas/`` because that directory is the public
output contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
SCHEMA_FILES = sorted(SCHEMAS_DIR.glob("*.json"))

SCHEMA_ID_PREFIX = "urn:a11y-devs:schema:"


def test_schemas_directory_is_not_empty() -> None:
    assert SCHEMA_FILES, "no schema files found"


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_schema_parses_as_utf8_json(path: Path) -> None:
    raw = path.read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM makes json.loads fail"
    assert b"\r" not in raw, "CRLF line endings leak into the published artifact"

    offending = {
        offset: byte
        for offset, byte in enumerate(raw)
        if byte < 0x20 and byte not in (0x09, 0x0A)
    }
    assert not offending, f"control characters in {path.name}: {offending}"

    json.loads(raw.decode("utf-8"))


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_schema_declares_a_versioned_identifier(path: Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))

    assert schema.get("$schema"), path.name
    schema_id = schema.get("$id", "")
    assert schema_id.startswith(SCHEMA_ID_PREFIX), path.name
    # The identifier must carry a version so consumers can pin it.
    assert schema_id.rsplit(":", 1)[-1][0].isdigit(), path.name


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_schema_declares_an_object_type(path: Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))

    assert schema.get("type") == "object", path.name
