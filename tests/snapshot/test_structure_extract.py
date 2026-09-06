"""Snapshot tests against the reference dataset.

Two different kinds of drift can change extraction output, and they are tested
separately:

* code drift — a change in this repository altering normalization. Compared
  against baselines in `snapshots/`, recorded from this codebase.
* provider drift — a Docling upgrade producing different structure. Compared
  against the manifests published by acessilia-structure-extractor, which were
  produced with an older Docling, so differences are reported, not asserted.

Requires docling-serve and the dataset submodule; skipped when either is absent.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import pytest

from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import create_adapter
from tests.snapshot.comparator import compare

pytestmark = pytest.mark.snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "tests" / "fixtures" / "dataset"
DOCUMENTS_DIR = DATASET_DIR / "input"
EXPECTED_DIR = DATASET_DIR / "intermediate" / "processing-manifest"
MANIFEST_CSV = DATASET_DIR / "intermediate" / "manifest.csv"
BASELINE_DIR = Path(__file__).parent / "snapshots"
CAPABILITY = "document.structure.extract"

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

# Long documents cost minutes per run and add little semantic coverage. The
# page count comes from the expected manifest, so no file is opened to decide.
MAX_PAGES = int(os.getenv("SNAPSHOT_MAX_PAGES", "30"))


def page_count(expected: Path) -> int:
    try:
        summary = json.loads(expected.read_text(encoding="utf-8")).get("summary", {})
    except (OSError, json.JSONDecodeError):
        return 0
    return int(summary.get("page_count", 0))


def source_for(document_id: str) -> Path | None:
    matches = [
        path
        for path in sorted(DOCUMENTS_DIR.glob(f"{document_id}.*"))
        if path.suffix.lower() in MEDIA_TYPES
    ]
    return matches[0] if matches else None


def discover_baselines() -> list[tuple[str, Path, Path]]:
    if not BASELINE_DIR.is_dir() or not DOCUMENTS_DIR.is_dir():
        return []
    fixtures = []
    for baseline in sorted(BASELINE_DIR.glob("*.json")):
        source = source_for(baseline.stem)
        if source is not None:
            fixtures.append((baseline.stem, source, baseline))
    return fixtures


def discover_reference() -> list[tuple[str, Path, Path]]:
    if not EXPECTED_DIR.is_dir() or not DOCUMENTS_DIR.is_dir():
        return []
    fixtures = []
    for expected in sorted(EXPECTED_DIR.glob("*.json")):
        if page_count(expected) > MAX_PAGES:
            continue
        source = source_for(expected.stem)
        if source is not None:
            fixtures.append((expected.stem, source, expected))
    return fixtures


BASELINES = discover_baselines()
REFERENCES = discover_reference()


@pytest.fixture(scope="module")
def executor() -> CapabilityExecutor:
    endpoint = os.getenv("DOCLING_SERVE_URL", "http://localhost:5001")
    descriptor = ProviderDescriptor.model_validate(
        {"id": "docling", "endpoint": endpoint, "capabilities": [CAPABILITY]}
    )
    if not create_adapter(descriptor).health().healthy:
        pytest.skip(f"docling-serve unreachable at {endpoint}")

    return CapabilityExecutor(
        CapabilityRegistry.from_directory(PROJECT_ROOT / "capabilities"),
        ProviderRegistry([descriptor]),
        create_adapter,
    )


def extract(executor: CapabilityExecutor, source: Path) -> Any:
    return executor.execute(
        CAPABILITY,
        source.read_bytes(),
        filename=source.name,
        media_type=MEDIA_TYPES[source.suffix.lower()],
    )


@pytest.mark.skipif(not DATASET_DIR.is_dir(), reason="dataset submodule not initialized")
def test_dataset_is_available() -> None:
    assert REFERENCES, (
        "no dataset fixtures found; run "
        "`git submodule update --init tests/fixtures/dataset`"
    )


@pytest.mark.skipif(not BASELINES, reason="no recorded baselines")
@pytest.mark.parametrize(
    "document_id,source,baseline", BASELINES, ids=[f[0] for f in BASELINES]
)
def test_output_matches_the_recorded_baseline(
    executor: CapabilityExecutor, document_id: str, source: Path, baseline: Path
) -> None:
    """Guards against drift introduced by this repository."""
    recorded = json.loads(baseline.read_text(encoding="utf-8"))
    result = extract(executor, source)

    if recorded["provider_versions"] != result.provenance.model_versions:
        pytest.skip(
            f"baseline recorded with {recorded['provider_versions']}, "
            f"provider now reports {result.provenance.model_versions}; "
            "re-record with scripts/record_snapshots.py"
        )

    diffs = compare(recorded["document"], result.document)

    assert not diffs, f"{document_id} drifted from its baseline:\n" + "\n".join(diffs)


@pytest.mark.skipif(not REFERENCES, reason="dataset submodule not initialized")
@pytest.mark.parametrize(
    "document_id,source,expected_path", REFERENCES, ids=[f[0] for f in REFERENCES]
)
def test_reference_manifests_are_reported(
    executor: CapabilityExecutor,
    document_id: str,
    source: Path,
    expected_path: Path,
    record_property: Any,
) -> None:
    """Reports divergence from the original extractor's published manifests.

    Those were produced with an older Docling, so differences are recorded
    rather than asserted; the baseline test above is what fails on regression.
    """
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    result = extract(executor, source)
    diffs = compare(expected, result.document)

    record_property("reference_diffs", len(diffs))
    if diffs:
        print(f"\n{document_id}: {len(diffs)} difference(s) from the reference manifest")
        for diff in diffs[:10]:
            print(f"  - {diff}")


@pytest.mark.skipif(not MANIFEST_CSV.is_file(), reason="dataset manifest missing")
def test_expected_manifests_are_declared_in_the_dataset_index() -> None:
    declared = {
        row["input_id"]
        for row in _dataset_index()
        if row.get("intermediate_type") == "processing-manifest"
    }

    for document_id, _, _ in REFERENCES:
        assert document_id in declared, f"{document_id} is missing from manifest.csv"


@pytest.mark.skipif(not MANIFEST_CSV.is_file(), reason="dataset manifest missing")
def test_reference_manifests_predate_the_current_provider() -> None:
    """Documents why reference differences are reported instead of asserted."""
    versions = {
        row["extractor_version"]
        for row in _dataset_index()
        if row.get("intermediate_type") == "processing-manifest"
        and row.get("extractor_version")
    }

    assert versions, "dataset does not record the extractor version"
    assert versions == {"2.87.0"}, (
        f"reference manifests were produced with {versions}; update the note in "
        "this module if the dataset is re-recorded"
    )


def _dataset_index() -> list[dict[str, str]]:
    with MANIFEST_CSV.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
