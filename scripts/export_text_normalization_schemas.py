"""Publish version-1 normalization schemas from their strict operational DTOs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from acessilia_toolbox.operations.text_normalization import (
    NormalizationRequest,
    NormalizationResult,
)

ROOT = Path(__file__).resolve().parents[1]


def outputs() -> dict[Path, str]:
    files = {}
    for name, model in (
        ("text-normalization-request", NormalizationRequest),
        ("text-normalization-result", NormalizationResult),
    ):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:a11y-devs:schema:{name}:1.0.0",
            **model.model_json_schema(),
        }
        files[ROOT / "schemas" / f"{name}@1.json"] = (
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
        )
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, content in outputs().items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                print(f"stale: {path.relative_to(ROOT)}")
                return 1
        else:
            path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
