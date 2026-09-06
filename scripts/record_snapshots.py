#!/usr/bin/env python3
"""Record snapshot baselines from the current codebase and provider.

Baselines pin the output of this repository so unintended normalization
changes surface in review. Re-record only after inspecting a diff and
deciding the change is intended, or after a deliberate provider upgrade.

Usage:
    python scripts/record_snapshots.py --check
    python scripts/record_snapshots.py --update
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.snapshot.comparator import compare  # noqa: E402

from acessilia_toolbox.core.capability import CapabilityRegistry  # noqa: E402
from acessilia_toolbox.core.executor import CapabilityExecutor  # noqa: E402
from acessilia_toolbox.core.provider import (  # noqa: E402
    ProviderDescriptor,
    ProviderRegistry,
)
from acessilia_toolbox.providers import create_adapter  # noqa: E402

DOCUMENTS_DIR = PROJECT_ROOT / "tests" / "fixtures" / "dataset" / "input"
EXPECTED_DIR = (
    PROJECT_ROOT / "tests" / "fixtures" / "dataset" / "intermediate" / "processing-manifest"
)
BASELINE_DIR = PROJECT_ROOT / "tests" / "snapshot" / "snapshots"
CAPABILITY = "document.structure.extract"

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def build_executor(endpoint: str) -> CapabilityExecutor:
    descriptor = ProviderDescriptor.model_validate(
        {"id": "docling", "endpoint": endpoint, "capabilities": [CAPABILITY]}
    )
    health = create_adapter(descriptor).health()
    if not health.healthy:
        raise SystemExit(f"docling-serve unreachable at {endpoint}: {health.detail}")
    return CapabilityExecutor(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"),
        ProviderRegistry([descriptor]),
        create_adapter,
    )


def documents(max_pages: int) -> list[Path]:
    selected = []
    for expected in sorted(EXPECTED_DIR.glob("*.json")):
        summary = json.loads(expected.read_text(encoding="utf-8")).get("summary", {})
        if int(summary.get("page_count", 0)) > max_pages:
            continue
        matches = [
            path
            for path in sorted(DOCUMENTS_DIR.glob(f"{expected.stem}.*"))
            if path.suffix.lower() in MEDIA_TYPES
        ]
        if matches:
            selected.append(matches[0])
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docling-serve", default="http://localhost:5001", help="Provider endpoint."
    )
    parser.add_argument("--max-pages", type=int, default=30, help="Skip longer documents.")
    parser.add_argument("--update", action="store_true", help="Rewrite the baselines.")
    parser.add_argument("--check", action="store_true", help="Report drift and exit.")
    args = parser.parse_args()

    if not args.update and not args.check:
        parser.error("choose --check or --update")

    executor = build_executor(args.docling_serve)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    drifted = 0
    for source in documents(args.max_pages):
        result = executor.execute(
            CAPABILITY,
            source.read_bytes(),
            filename=source.name,
            media_type=MEDIA_TYPES[source.suffix.lower()],
        )
        payload = {
            "provider_versions": result.provenance.model_versions,
            "document": result.document,
        }
        baseline = BASELINE_DIR / f"{source.stem}.json"

        if args.update:
            baseline.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(
                f"  recorded {source.stem}: "
                f"{result.document['summary']['element_count']} elements"
            )
            continue

        if not baseline.is_file():
            print(f"  {source.stem}: no baseline recorded")
            drifted += 1
            continue

        recorded = json.loads(baseline.read_text(encoding="utf-8"))
        diffs = compare(recorded["document"], result.document)
        if diffs:
            drifted += 1
            print(f"  {source.stem}: {len(diffs)} difference(s)")
            for diff in diffs[:10]:
                print(f"      - {diff}")
        else:
            print(f"  {source.stem}: unchanged")

    if args.check and drifted:
        print(f"\n{drifted} document(s) drifted from their baselines")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
