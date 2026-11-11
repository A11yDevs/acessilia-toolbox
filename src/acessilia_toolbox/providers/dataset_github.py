"""GitHub provider for versioned Acessilia Dataset revisions.

The references manifest is the source of truth for annotation applicability.
Input paths and reference selectors are read from the repository manifests;
the provider never derives artifact names from an item ID.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from threading import Lock
from typing import Any
from urllib.parse import quote, unquote

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable

from acessilia_toolbox.core.dataset import (
    DatasetArtifactRef,
    DatasetInfo,
    DatasetItem,
    ItemSummary,
    SplitInfo,
)
from acessilia_toolbox.core.errors import (
    DatasetNotFoundError,
    DatasetProviderError,
    InvalidInputError,
    ItemNotFoundError,
    SplitNotFoundError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor

DATASET_REFERENCE_REVISION = "855e9e927e606328203e87de74b1afcc6014e5b4"
LEGACY_DATASET_REVISION = "9ed07b28aeddb69721228f2dd66de6f9794c0001"
REFERENCE_MANIFEST_SCHEMA_VERSION = "1.2.0"
DATASET_METADATA_SCHEMA = "acessilia-dataset-item-metadata@1"
DATASET_PROVIDER_CONTRACT_VERSION = "10"
REFERENCE_MANIFEST_PATH = "references/manifest.csv"
REFERENCE_MANIFEST_SCHEMA_PATH = "references/manifest.schema.json"
LEGACY_FORMULA_REFERENCES = "input/formula-images/ground_truth.csv"
REFERENCE_MANIFEST_COLUMNS = (
    "input_id",
    "task",
    "availability",
    "coverage_scope",
    "annotated_count",
    "eligible_count",
    "coverage_unit",
    "review_state",
    "reviewer",
    "reviewed_at",
    "provenance",
    "annotation_revision",
    "reference_type",
    "format",
    "schema_version",
    "content_schema",
    "file_path",
    "record_selector",
    "notes",
)
# Task-specific selectors in the Dataset 1.2 contract.
TASK_JSON_SELECTORS = {
    "math_text_quality": ("rules", {"/text"}),
    "math_document_structure": ("rules", {"/structure"}),
    "math_formula_speech": ("items", {"/speech/formulas"}),
    "presentation_output_safety": ("items", {"/text/must_not_match"}),
    "presentation_structure": (
        "rules", {"/structure/slide_titles", "/structure/deck_title", "/structure/slide_count"}
    ),
    "presentation_degraded_run_behavior": ("rules", {"/degraded"}),
}
CSV_ONLY_TASKS = {
    "formula_image_recognition", "math_formula_transcription", "presentation_slide_content",
}
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

DATASET_DEFS: dict[str, dict[str, Any]] = {
    "acessilia-dataset": {
        "name": "Acessilia Dataset",
        "description": (
            "Reference dataset for accessibility processing pipelines. "
            "Contains source documents and versioned references describing "
            "annotation availability, coverage, and applicable records."
        ),
        "source": "https://github.com/A11yDevs/acessilia-dataset",
        "source_type": "github",
        "default_revision": DATASET_REFERENCE_REVISION,
        "owner": "A11yDevs",
        "repo": "acessilia-dataset",
        "input_manifest_path": "input/manifest.csv",
        # Legacy split manifests remain readable where the revision contains
        # them, including the transition revision. They are marked deprecated
        # while exposed; a references-only revision advertises only input.
        "legacy_splits": {
            "input": {
                "name": "Input Documents",
                "description": "Source documents for testing",
                "manifest_path": "input/manifest.csv",
            },
            "intermediate": {
                "name": "Intermediate Artifacts",
                "description": "Historical processing artifacts",
                "manifest_path": "intermediate/manifest.csv",
            },
            "outputs": {
                "name": "Accessible Outputs",
                "description": "Historical generated output files",
                "manifest_path": "outputs/manifest.csv",
            },
        },
    },
}


@dataclass(frozen=True)
class _RevisionContext:
    requested_revision: str
    effective_revision: str
    layout: str
    input_rows: list[dict[str, str]]
    reference_rows: list[dict[str, str]]
    legacy_rows: dict[str, list[dict[str, str]]]
    legacy_formula_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _ReferenceDocument:
    payload: bytes
    media_type: str
    records: Any


LEGACY_NUMBERED_INPUTS = {f"{number:03d}" for number in range(1, 9)}


def _legacy_input_path(row: dict[str, str]) -> str | None:
    """Resolve original filenames in the published legacy Dataset layout."""
    filename = row.get("original_filename")
    if not filename:
        return None
    subdirectory = row.get("subdirectory", "")
    if not subdirectory and row["id"] in LEGACY_NUMBERED_INPUTS:
        filename = row["id"] + PurePosixPath(filename).suffix
    return "/".join(part for part in ("input", subdirectory, filename) if part)


def _selected_json_count(value: Any, selector: dict[str, Any]) -> int:
    count = 0
    mode = selector["count_mode"]
    for pointer in selector["pointers"]:
        selected = value
        for part in pointer[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(selected, dict):
                selected = selected[key]
            elif isinstance(selected, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
                selected = selected[int(key)]
            else:
                raise ValueError(f"unresolved JSON pointer {pointer!r}")
        if isinstance(selected, list):
            count += len(selected)
        elif mode == "items":
            raise ValueError(f"selector {pointer!r} must identify a list")
        elif mode == "rules" and isinstance(selected, dict):
            meaningful = [
                child for key, child in selected.items()
                if key not in {"description", "notes", "why", "language"}
            ]
            if "description" in selected or any(isinstance(child, list) for child in meaningful):
                count += sum(len(child) if isinstance(child, list) else 1 for child in meaningful)
            else:
                count += int(bool(meaningful))
        else:
            count += 1
    return count


def _validate_repo_path(value: str, name: str) -> str:
    """Reject path traversal before a URL can carry the GitHub token elsewhere."""
    decoded = value
    while True:
        if (
            not decoded
            or decoded.startswith("/")
            or "\\" in decoded
            or "\x00" in decoded
            or any(segment in {"", ".", ".."} for segment in decoded.split("/"))
        ):
            raise InvalidInputError(
                f"{name} must be a relative repository path without empty or dot segments",
                parameter=name,
            )
        normalized = unquote(decoded)
        if normalized == decoded:
            return value
        decoded = normalized


class GitHubDatasetProvider:
    """Fetch versioned dataset manifests and artifacts from GitHub."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        default_revision = descriptor.config.get("default_revision", DATASET_REFERENCE_REVISION)
        if not isinstance(default_revision, str) or not _SHA_RE.fullmatch(default_revision):
            raise ValueError("dataset-github config.default_revision must be a full commit SHA")
        self.descriptor = descriptor
        self._http = httpx.Client(timeout=30.0, follow_redirects=True)
        self._github_token = os.getenv("GITHUB_TOKEN", "")
        self._defs = dict(DATASET_DEFS)
        self._defs["acessilia-dataset"] = {
            **DATASET_DEFS["acessilia-dataset"],
            "default_revision": default_revision.lower(),
        }
        self._manifest_cache: dict[
            tuple[str, str, str], list[dict[str, str]] | None
        ] = {}
        self._manifest_headers: dict[tuple[str, str, str], list[str]] = {}
        self._validated_reference_manifests: set[tuple[str, str]] = set()
        self._verified_files: set[tuple[str, str, str]] = set()
        self._reference_documents: OrderedDict[
            tuple[str, str, str], _ReferenceDocument
        ] = OrderedDict()
        self._content_validators: OrderedDict[
            tuple[str, str, str], Draft202012Validator
        ] = OrderedDict()
        self._reference_lock = Lock()

    @property
    def cache_namespace(self) -> str:
        """Identity for default-revision list results and metadata schema changes."""
        default = self._defs["acessilia-dataset"]["default_revision"]
        return (
            f"github-dataset-provider@{DATASET_PROVIDER_CONTRACT_VERSION}:"
            f"{DATASET_METADATA_SCHEMA}:{default}"
        )

    def resolve_revision(self, dataset_id: str, revision: str | None = None) -> str:
        """Resolve a mutable Git reference to the immutable commit used by a call."""
        ddef = self._resolve_def(dataset_id)
        requested = revision or ddef["default_revision"]
        if _SHA_RE.fullmatch(requested):
            return requested.lower()

        owner, repo = ddef["owner"], ddef["repo"]
        encoded_ref = quote(_validate_repo_path(requested, "revision"), safe="/")
        url = f"https://api.github.com/repos/{owner}/{repo}/commits/{encoded_ref}"
        response = self._http.get(url, headers=self._headers(json_api=True))
        if response.status_code != 200:
            raise DatasetProviderError(
                f"could not resolve dataset revision {requested!r}",
                dataset=dataset_id,
                revision=requested,
                status=response.status_code,
            )
        try:
            commit = response.json()["sha"]
        except (ValueError, KeyError, TypeError) as exc:
            raise DatasetProviderError(
                "GitHub returned an invalid commit response",
                dataset=dataset_id,
                revision=requested,
            ) from exc
        if not isinstance(commit, str) or not _SHA_RE.fullmatch(commit):
            raise DatasetProviderError(
                "GitHub returned a commit without a full SHA",
                dataset=dataset_id,
                revision=requested,
            )
        return commit.lower()

    def list_datasets(self) -> Sequence[DatasetInfo]:
        return [self.describe(dataset_id) for dataset_id in self._defs]

    def describe(self, dataset_id: str, revision: str | None = None) -> DatasetInfo:
        context = self._load_context(dataset_id, revision)
        info = self._build_info(dataset_id, self._resolve_def(dataset_id))
        info.revisions = sorted(
            {
                info.default_revision,
                context.effective_revision,
            }
        )
        info.splits = self._available_splits(context)
        return info

    def list_splits(
        self, dataset_id: str, revision: str | None = None
    ) -> Sequence[SplitInfo]:
        context = self._load_context(dataset_id, revision)
        uses_references = context.layout != "legacy"
        split_infos: list[SplitInfo] = []

        if uses_references:
            formats = set()
            for row in context.reference_rows:
                if row["availability"] != "unannotated":
                    self._verify_reference(
                        dataset_id, context.effective_revision, row,
                        json.loads(row["record_selector"]),
                    )
                    formats.add(self._media_type(row["format"]))
            split_infos.append(
                SplitInfo(
                    id="input",
                    name="Input Documents",
                    description=(
                        "Source documents with per-task reference coverage in "
                        "the versioned references manifest"
                    ),
                    item_count=len(context.input_rows),
                    has_ground_truth=bool(formats),
                    artifact_types=sorted(
                        {
                            self._media_type(row.get("media_type", ""))
                            for row in context.input_rows
                        }
                        | formats
                    ),
                )
            )

        for split in self._available_splits(context):
            if split == "input" and uses_references:
                continue
            rows = context.input_rows if split == "input" else context.legacy_rows[split]
            ground_truth = (
                bool(context.legacy_formula_ids)
                if split == "input"
                else split == "outputs" and any(self._is_output_available(row) for row in rows)
            )
            split_infos.append(
                SplitInfo(
                    id=split,
                    name=self._resolve_def(dataset_id)["legacy_splits"][split]["name"],
                    description=self._resolve_def(dataset_id)["legacy_splits"][split][
                        "description"
                    ],
                    item_count=len(self._group_rows(split, rows)),
                    has_ground_truth=ground_truth,
                    deprecated=split in {"intermediate", "outputs"},
                    artifact_types=self._artifact_types_for_split(split),
                )
            )
        return split_infos

    def list_items(
        self,
        dataset_id: str,
        split: str,
        revision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ItemSummary]:
        context = self._load_context(dataset_id, revision)
        rows = self._rows_for_split(context, split)
        groups = self._group_rows(split, rows)[offset : offset + limit]
        items = [
            self._build_item(context, dataset_id, split, item_id, item_rows)
            for item_id, item_rows in groups
        ]
        return [
            ItemSummary(
                id=item.id,
                dataset=item.dataset,
                split=item.split,
                revision=item.revision,
                artifact_count=self._artifact_count(item),
                has_annotations=bool(item.annotations),
                metadata=item.metadata,
            )
            for item in items
        ]

    def get_item(
        self,
        dataset_id: str,
        item_id: str,
        split: str,
        revision: str | None = None,
    ) -> DatasetItem:
        context = self._load_context(dataset_id, revision)
        rows = self._rows_for_split(context, split)
        group = next(
            (
                item_rows
                for candidate_id, item_rows in self._group_rows(split, rows)
                if candidate_id == item_id
            ),
            None,
        )
        if group is None:
            raise ItemNotFoundError(
                f"item {item_id!r} not found in {dataset_id}/{split}",
                dataset=dataset_id,
                split=split,
                item=item_id,
            )
        return self._build_item(context, dataset_id, split, item_id, group)

    def get_artifact(
        self,
        dataset_id: str,
        artifact_path: str,
        revision: str | None = None,
    ) -> tuple[bytes, str]:
        effective_revision = self.resolve_revision(dataset_id, revision)
        cached = self._reference_documents.get((dataset_id, effective_revision, artifact_path))
        if cached is not None:
            return cached.payload, cached.media_type
        url = self._artifact_url(dataset_id, effective_revision, artifact_path)
        response = self._http.get(url, headers=self._headers())
        if response.status_code == 404:
            raise ItemNotFoundError(
                f"artifact not found at revision {effective_revision}: {artifact_path}",
                dataset=dataset_id,
                path=artifact_path,
                revision=effective_revision,
            )
        if response.status_code != 200:
            raise DatasetProviderError(
                f"GitHub returned {response.status_code} for {artifact_path}",
                dataset=dataset_id,
                path=artifact_path,
                revision=effective_revision,
                status=response.status_code,
            )
        return response.content, response.headers.get(
            "content-type", "application/octet-stream"
        )

    def _artifact_url(self, dataset_id: str, revision: str, path: str) -> str:
        ddef = self._resolve_def(dataset_id)
        encoded_path = quote(_validate_repo_path(path, "artifact_path"), safe="/")
        return f"https://raw.githubusercontent.com/{ddef['owner']}/{ddef['repo']}/{revision}/{encoded_path}"

    def _verify_file(self, dataset_id: str, revision: str, path: str, *, kind: str) -> None:
        key = (dataset_id, revision, path)
        if key in self._verified_files:
            return
        response = self._http.head(
            self._artifact_url(dataset_id, revision, path), headers=self._headers()
        )
        if response.status_code != 200:
            raise DatasetProviderError(
                f"required {kind} file is unavailable at revision {revision}: {path}",
                dataset=dataset_id,
                revision=revision,
                path=path,
                status=response.status_code,
                reason=(
                    f"required_{kind}_missing"
                    if response.status_code == 404 else f"{kind}_unavailable"
                ),
            )
        self._verified_files.add(key)

    def _verify_reference(
        self,
        dataset_id: str,
        revision: str,
        reference: dict[str, str],
        selector: dict[str, Any],
    ) -> None:
        path = reference["file_path"]
        format_name = reference["format"]
        key = (dataset_id, revision, path)
        validator = self._content_validator(dataset_id, revision, reference["content_schema"])
        try:
            cached = self._reference_documents.get(key)
            if cached is None:
                payload, media_type = self.get_artifact(dataset_id, path, revision)
                text = payload.decode("utf-8-sig")
                records = (
                    list(csv.DictReader(io.StringIO(text), strict=True))
                    if format_name == "csv"
                    else json.loads(text)
                )
                cached = _ReferenceDocument(payload, media_type, records)
                with self._reference_lock:
                    self._reference_documents[key] = cached
                    if len(self._reference_documents) > 32:
                        self._reference_documents.popitem(last=False)
            input_id = reference["input_id"]
            if format_name == "csv":
                filters = selector["filters"]
                selected = [
                    row for row in cached.records
                    if all(row.get(column) == value for column, value in filters.items())
                ]
                for row in selected:
                    validator.validate(row)
                count = len(selected)
            else:
                if (
                    not isinstance(cached.records, dict)
                    or cached.records.get("manifest_id") != input_id
                ):
                    raise ValueError(f"JSON reference must identify input {input_id}")
                validator.validate(cached.records)
                count = _selected_json_count(cached.records, selector)
            if count != int(reference["annotated_count"]):
                raise ValueError(
                    f"selector resolved to {count} records, expected {reference['annotated_count']}"
                )
        except ItemNotFoundError as exc:
            raise DatasetProviderError(
                f"required reference file is unavailable at revision {revision}: {path}",
                dataset=dataset_id, revision=revision, path=path,
                reason="required_reference_missing",
            ) from exc
        except (ValueError, KeyError, IndexError, csv.Error, ValidationError, Unresolvable) as exc:
            detail = exc.message if isinstance(exc, ValidationError) else str(exc)
            raise DatasetProviderError(
                f"invalid reference for input {reference['input_id']} "
                f"task {reference['task']}: {detail}",
                dataset=dataset_id, revision=revision, path=path,
                reason="invalid_reference",
            ) from exc

    def _content_validator(
        self, dataset_id: str, revision: str, path: str
    ) -> Draft202012Validator:
        key = (dataset_id, revision, path)
        cached = self._content_validators.get(key)
        if cached is not None:
            return cached
        try:
            content, _ = self.get_artifact(dataset_id, path, revision)
            schema = json.loads(content)
            Draft202012Validator.check_schema(schema)
        except ItemNotFoundError as exc:
            raise DatasetProviderError(
                f"required reference file is unavailable at revision {revision}: {path}",
                dataset=dataset_id, revision=revision, path=path,
                reason="required_reference_missing",
            ) from exc
        except (ValueError, SchemaError) as exc:
            raise DatasetProviderError(
                f"invalid reference content schema {path}: {exc}",
                dataset=dataset_id, revision=revision, path=path,
                reason="invalid_reference_schema",
            ) from exc
        validator = Draft202012Validator(schema, registry=Registry())
        with self._reference_lock:
            self._content_validators[key] = validator
            if len(self._content_validators) > 32:
                self._content_validators.popitem(last=False)
        return validator

    def sample(
        self,
        dataset_id: str,
        split: str,
        n: int = 5,
        revision: str | None = None,
    ) -> Sequence[DatasetItem]:
        import random

        context = self._load_context(dataset_id, revision)
        groups = self._group_rows(split, self._rows_for_split(context, split))
        return [
            self._build_item(context, dataset_id, split, item_id, rows)
            for item_id, rows in random.sample(groups, min(n, len(groups)))
        ]

    def _headers(self, *, json_api: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if json_api:
            headers["Accept"] = "application/vnd.github+json"
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"
        return headers

    def _resolve_def(self, dataset_id: str) -> dict[str, Any]:
        ddef = self._defs.get(dataset_id)
        if ddef is None:
            raise DatasetNotFoundError(
                f"unknown dataset {dataset_id!r}",
                dataset=dataset_id,
                known=list(self._defs),
            )
        return ddef

    def _build_info(self, dataset_id: str, ddef: dict[str, Any]) -> DatasetInfo:
        return DatasetInfo(
            id=dataset_id,
            name=ddef["name"],
            description=ddef["description"],
            source=ddef["source"],
            source_type=ddef["source_type"],
            default_revision=ddef["default_revision"],
            revisions=[ddef["default_revision"]],
            splits=["input"],
        )

    def _load_context(
        self, dataset_id: str, revision: str | None
    ) -> _RevisionContext:
        ddef = self._resolve_def(dataset_id)
        requested = revision or ddef["default_revision"]
        effective = self.resolve_revision(dataset_id, requested)

        input_rows = self._fetch_manifest(
            dataset_id,
            ddef["input_manifest_path"],
            effective,
            required=True,
            required_columns=("id",),
        )
        assert input_rows is not None  # A missing required manifest raises above.
        input_ids = [row.get("id", "") for row in input_rows]
        if any(not item_id for item_id in input_ids) or len(input_ids) != len(
            set(input_ids)
        ):
            raise DatasetProviderError(
                "input manifest contains a missing or duplicate string ID",
                dataset=dataset_id,
                revision=effective,
                path=ddef["input_manifest_path"],
                reason="invalid_manifest",
            )
        reference_rows = self._fetch_manifest(
            dataset_id,
            REFERENCE_MANIFEST_PATH,
            effective,
            required=False,
            required_columns=REFERENCE_MANIFEST_COLUMNS,
        )
        legacy_rows = {"input": input_rows}
        if reference_rows is not None:
            self._validate_reference_rows(dataset_id, effective, reference_rows)
            input_id_set = set(input_ids)
            orphaned = sorted(
                {row["input_id"] for row in reference_rows} - input_id_set
            )
            if orphaned:
                raise DatasetProviderError(
                    "references manifest points to input IDs absent from input/manifest.csv",
                    dataset=dataset_id,
                    revision=effective,
                    path=REFERENCE_MANIFEST_PATH,
                    input_ids=orphaned,
                    reason="invalid_manifest",
                )
            for split in ("intermediate", "outputs"):
                split_def = ddef["legacy_splits"][split]
                split_rows = self._fetch_manifest(
                    dataset_id,
                    split_def["manifest_path"],
                    effective,
                    required=False,
                    required_columns=("input_id", "file_path"),
                )
                if split_rows is not None:
                    legacy_rows[split] = split_rows
            layout = (
                "references-v1.2"
                if len(legacy_rows) == 1
                else "transition-v1.2"
            )
            return _RevisionContext(
                requested_revision=requested,
                effective_revision=effective,
                layout=layout,
                input_rows=input_rows,
                reference_rows=reference_rows,
                legacy_rows=legacy_rows,
            )

        input_headers = self._manifest_headers.get(
            (dataset_id, effective, ddef["input_manifest_path"]), []
        )
        if effective == DATASET_REFERENCE_REVISION or "input_path" in input_headers:
            raise DatasetProviderError(
                "new input manifest requires references/manifest.csv; "
                "legacy fallback is disabled",
                dataset=dataset_id,
                revision=effective,
                path=REFERENCE_MANIFEST_PATH,
                reason="required_manifest_missing",
            )

        for split in ("intermediate", "outputs"):
            split_def = ddef["legacy_splits"][split]
            split_rows = self._fetch_manifest(
                dataset_id,
                split_def["manifest_path"],
                effective,
                required=False,
                required_columns=("input_id", "file_path"),
            )
            if split_rows is not None:
                legacy_rows[split] = split_rows
        formulas = None
        require_formula_manifest = effective == LEGACY_DATASET_REVISION
        if require_formula_manifest or any(
            row.get("subdirectory") == "formula-images" for row in input_rows
        ):
            formulas = self._fetch_manifest(
                dataset_id, LEGACY_FORMULA_REFERENCES, effective,
                required=require_formula_manifest, required_columns=("manifest_id",),
            )
        return _RevisionContext(
            requested_revision=requested,
            effective_revision=effective,
            layout="legacy",
            input_rows=input_rows,
            reference_rows=[],
            legacy_rows=legacy_rows,
            legacy_formula_ids=frozenset(row["manifest_id"] for row in formulas or ()),
        )

    def _fetch_manifest(
        self,
        dataset_id: str,
        manifest_path: str,
        revision: str,
        *,
        required: bool,
        required_columns: Sequence[str],
    ) -> list[dict[str, str]] | None:
        """Fetch and parse a required or optional manifest at an immutable SHA."""
        key = (dataset_id, revision, manifest_path)
        if key in self._manifest_cache:
            cached = self._manifest_cache[key]
            if cached is None and required:
                raise self._missing_manifest(dataset_id, revision, manifest_path)
            return cached

        try:
            content, _ = self.get_artifact(dataset_id, manifest_path, revision)
        except ItemNotFoundError as exc:
            if not required:
                self._manifest_cache[key] = None
                return None
            raise self._missing_manifest(dataset_id, revision, manifest_path) from exc

        try:
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            headers = reader.fieldnames
            if (
                not headers
                or any(not header for header in headers)
                or len(headers) != len(set(headers))
            ):
                raise ValueError("CSV header is missing or contains duplicates")
            if (
                manifest_path == REFERENCE_MANIFEST_PATH
                and set(headers) != set(REFERENCE_MANIFEST_COLUMNS)
            ):
                raise ValueError(
                    "references manifest columns do not match schema "
                    + REFERENCE_MANIFEST_SCHEMA_VERSION
                )
            missing = sorted(set(required_columns) - set(headers))
            if missing:
                raise ValueError(f"missing required columns: {', '.join(missing)}")
            rows: list[dict[str, str]] = []
            for line_number, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"malformed row at line {line_number}")
                rows.append(dict(row))
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise DatasetProviderError(
                f"invalid dataset manifest {manifest_path}: {exc}",
                dataset=dataset_id,
                revision=revision,
                path=manifest_path,
                reason="invalid_manifest",
            ) from exc

        self._manifest_headers[key] = list(headers)
        self._manifest_cache[key] = rows
        return rows

    @staticmethod
    def _missing_manifest(
        dataset_id: str, revision: str, manifest_path: str
    ) -> DatasetProviderError:
        return DatasetProviderError(
            f"required dataset manifest is missing: {manifest_path}",
            dataset=dataset_id,
            revision=revision,
            path=manifest_path,
            reason="required_manifest_missing",
        )

    def _reference_manifest_validator(
        self, dataset_id: str, revision: str
    ) -> Draft202012Validator:
        path = REFERENCE_MANIFEST_SCHEMA_PATH
        try:
            content, _ = self.get_artifact(dataset_id, path, revision)
        except ItemNotFoundError as exc:
            raise self._missing_manifest(dataset_id, revision, path) from exc
        try:
            schema = json.loads(content)
            version = schema.get("x-manifest-schema-version") if isinstance(schema, dict) else None
            if not isinstance(version, str) or not version:
                raise ValueError("x-manifest-schema-version must be a non-empty string")
        except ValueError as exc:
            raise DatasetProviderError(
                f"invalid references manifest schema {path}: {exc}",
                dataset=dataset_id, revision=revision, path=path,
                reason="invalid_manifest_schema",
            ) from exc
        if version != REFERENCE_MANIFEST_SCHEMA_VERSION:
            raise DatasetProviderError(
                "unsupported references manifest schema version",
                dataset=dataset_id, revision=revision, path=path,
                schema_version=version, supported=REFERENCE_MANIFEST_SCHEMA_VERSION,
                reason="unsupported_manifest_schema",
            )
        try:
            if (
                schema.get("type") != "object"
                or schema.get("x-csv-columns") != list(REFERENCE_MANIFEST_COLUMNS)
            ):
                raise ValueError("expected the reference row type and CSV columns")
            Draft202012Validator.check_schema(schema)
        except (SchemaError, ValueError) as exc:
            raise DatasetProviderError(
                f"invalid references manifest schema {path}: {exc}",
                dataset=dataset_id, revision=revision, path=path,
                reason="invalid_manifest_schema",
            ) from exc
        # A schema read from a revision must not fetch external schemas.
        return Draft202012Validator(schema, registry=Registry())

    def _validate_reference_rows(
        self, dataset_id: str, revision: str, rows: list[dict[str, str]]
    ) -> None:
        manifest_key = (dataset_id, revision)
        if manifest_key in self._validated_reference_manifests:
            return
        validator = self._reference_manifest_validator(dataset_id, revision)
        seen: set[tuple[str, str, str, str, str]] = set()
        for line_number, row in enumerate(rows, start=2):
            if row.get("schema_version") != REFERENCE_MANIFEST_SCHEMA_VERSION:
                raise DatasetProviderError(
                    "unsupported references manifest schema version",
                    dataset=dataset_id,
                    revision=revision,
                    path=REFERENCE_MANIFEST_PATH,
                    line=line_number,
                    schema_version=row.get("schema_version", ""),
                    supported=REFERENCE_MANIFEST_SCHEMA_VERSION,
                    reason="unsupported_manifest_schema",
                )
            try:
                violation = next(validator.iter_errors(row), None)
            except Unresolvable as exc:
                raise DatasetProviderError(
                    f"invalid references manifest schema reference: {exc}",
                    dataset=dataset_id, revision=revision, path=REFERENCE_MANIFEST_SCHEMA_PATH,
                    reason="invalid_manifest_schema",
                ) from exc
            if violation is not None:
                field = ".".join(map(str, violation.absolute_path)) or "row"
                raise DatasetProviderError(
                    f"references manifest line {line_number}: invalid {field}: {violation.message}",
                    dataset=dataset_id, revision=revision, path=REFERENCE_MANIFEST_PATH,
                    line=line_number, reason="invalid_manifest",
                )
            key = (
                row.get("input_id", ""),
                row.get("task", ""),
                row.get("format", ""),
                row.get("file_path", ""),
                row.get("record_selector", ""),
            )
            if key in seen:
                raise DatasetProviderError(
                    "references manifest has a duplicate logical record",
                    dataset=dataset_id,
                    revision=revision,
                    path=REFERENCE_MANIFEST_PATH,
                    line=line_number,
                    input_id=key[0],
                    task=key[1],
                )
            seen.add(key)
            availability = row["availability"]
            path = row["file_path"]
            selector = row["record_selector"]
            raw_annotated = row["annotated_count"]
            raw_eligible = row["eligible_count"]
            if (
                (raw_eligible and int(raw_eligible) < int(raw_annotated))
                or (availability == "complete" and raw_eligible != raw_annotated)
            ):
                raise DatasetProviderError(
                    "references manifest has inconsistent coverage counts",
                    dataset=dataset_id,
                    revision=revision,
                    path=REFERENCE_MANIFEST_PATH,
                    line=line_number,
                    reason="invalid_manifest",
                )
            if availability == "unannotated":
                continue
            try:
                _validate_repo_path(path, "file_path")
                schema_path = _validate_repo_path(
                    row["content_schema"], "content_schema"
                )
                parsed_selector = json.loads(selector)
            except (InvalidInputError, KeyError, json.JSONDecodeError) as exc:
                raise DatasetProviderError(
                    "annotated reference row has an invalid path or record selector",
                    dataset=dataset_id,
                    revision=revision,
                    path=REFERENCE_MANIFEST_PATH,
                    line=line_number,
                ) from exc
            if not isinstance(parsed_selector, dict) or parsed_selector.get(
                "type"
            ) not in {"csv_filter", "json_pointers"}:
                raise DatasetProviderError(
                    "annotated reference row uses an unsupported record selector",
                    dataset=dataset_id,
                    revision=revision,
                    path=REFERENCE_MANIFEST_PATH,
                    line=line_number,
                )
            if not path.startswith("references/") or not (
                schema_path.startswith("references/") and schema_path.endswith(".schema.json")
            ):
                raise DatasetProviderError(
                    "reference and content schema must be files under references/",
                    dataset=dataset_id,
                    revision=revision,
                    path=REFERENCE_MANIFEST_PATH,
                    line=line_number,
                )
            selector_type = parsed_selector["type"]
            if selector_type == "csv_filter":
                valid_selector = (
                    set(parsed_selector) == {"type", "filters", "count_mode"}
                    and isinstance(parsed_selector.get("filters"), dict)
                    and bool(parsed_selector["filters"])
                    and all(
                        isinstance(key, str)
                        and key
                        and isinstance(value, str)
                        and value
                        for key, value in parsed_selector["filters"].items()
                    )
                    and parsed_selector.get("count_mode") == "rows"
                    and row["format"] == "csv"
                    and parsed_selector["filters"].get("manifest_id") == row["input_id"]
                )
            else:
                pointers = parsed_selector.get("pointers")
                valid_selector = (
                    set(parsed_selector) == {"type", "pointers", "count_mode"}
                    and isinstance(pointers, list)
                    and bool(pointers)
                    and all(
                        isinstance(pointer, str) and pointer.startswith("/")
                        for pointer in pointers
                    )
                    and len(pointers) == len(set(pointers))
                    and parsed_selector.get("count_mode")
                    in {"items", "rules", "units"}
                    and row["format"] == "json"
                )
            if not valid_selector or PurePosixPath(path).suffix.lower() != f".{row['format']}":
                raise DatasetProviderError(
                    "reference manifest has an invalid record selector",
                    dataset=dataset_id,
                    revision=revision,
                    path=REFERENCE_MANIFEST_PATH,
                    line=line_number,
                )
            task = row["task"]
            json_contract = TASK_JSON_SELECTORS.get(task)
            if selector_type == "csv_filter":
                matches_task = json_contract is None or (
                    task == "presentation_structure"
                    and parsed_selector["filters"].get("kind") == "table"
                )
            else:
                matches_task = task not in CSV_ONLY_TASKS and (
                    json_contract is None or (
                        parsed_selector["count_mode"] == json_contract[0]
                        and set(parsed_selector["pointers"]) <= json_contract[1]
                    )
                )
            if not matches_task:
                raise DatasetProviderError(
                    f"record selector does not match task {task!r}",
                    dataset=dataset_id, revision=revision, path=REFERENCE_MANIFEST_PATH,
                    line=line_number, reason="invalid_manifest",
                )
        self._validated_reference_manifests.add(manifest_key)

    def _available_splits(self, context: _RevisionContext) -> list[str]:
        if context.layout == "references-v1.2":
            return ["input"]
        return list(context.legacy_rows)

    def _rows_for_split(
        self, context: _RevisionContext, split: str
    ) -> list[dict[str, str]]:
        if split not in self._available_splits(context):
            raise SplitNotFoundError(
                f"split {split!r} is not supported by {context.layout} at "
                f"revision {context.effective_revision}",
                dataset="acessilia-dataset",
                split=split,
                revision=context.effective_revision,
                layout=context.layout,
                supported_splits=self._available_splits(context),
            )
        return context.input_rows if split == "input" else context.legacy_rows[split]

    @staticmethod
    def _group_rows(
        split: str, rows: list[dict[str, str]]
    ) -> list[tuple[str, list[dict[str, str]]]]:
        groups: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            item_id = row.get("id") if split == "input" else row.get("input_id")
            if not item_id:
                raise DatasetProviderError(
                    f"{split} manifest row is missing its string item ID",
                    split=split,
                    reason="invalid_manifest",
                )
            groups.setdefault(item_id, []).append(row)
        return list(groups.items())

    def _build_item(
        self,
        context: _RevisionContext,
        dataset_id: str,
        split: str,
        item_id: str,
        rows: list[dict[str, str]],
    ) -> DatasetItem:
        revision = context.effective_revision
        artifacts: list[DatasetArtifactRef] = []
        annotations: list[DatasetArtifactRef] = []
        bindings: list[dict[str, Any]] = []
        unannotated_tasks: list[str] = []

        if split == "input":
            source = self._input_artifact(rows[0], dataset_id, revision)
            artifacts.append(source)
            if context.layout != "legacy":
                for reference in context.reference_rows:
                    if reference["input_id"] != item_id:
                        continue
                    binding = self._reference_binding(reference)
                    bindings.append(binding)
                    if reference["availability"] == "unannotated":
                        unannotated_tasks.append(reference["task"])
                        continue
                    self._verify_reference(
                        dataset_id, revision, reference,
                        binding["record_selector"],
                    )
                    ref = DatasetArtifactRef(
                        path=reference["file_path"],
                        media_type=self._media_type(reference["format"]),
                    )
                    if ref.path not in {item.path for item in annotations}:
                        annotations.append(ref)
            else:
                for row in rows:
                    legacy_path = row.get("annotation_path") or row.get(
                        "ground_truth_path", ""
                    )
                    if legacy_path:
                        annotations.append(
                            DatasetArtifactRef(
                                path=_validate_repo_path(legacy_path, "annotation_path"),
                                media_type=self._media_type(
                                    row.get("annotation_format", "csv")
                                ),
                            )
                        )
                if item_id in context.legacy_formula_ids and not any(
                    ref.path == LEGACY_FORMULA_REFERENCES for ref in annotations
                ):
                    annotations.append(DatasetArtifactRef(
                        path=LEGACY_FORMULA_REFERENCES, media_type="text/csv"
                    ))
        else:
            for row in rows:
                path = row.get("file_path", "")
                if not path:
                    continue
                if split == "outputs" and not self._is_output_available(row):
                    continue
                if (
                    split == "intermediate"
                    and row.get("status", "").strip()
                    and not self._is_output_available(row)
                ):
                    continue
                if split == "intermediate" and not path.startswith("intermediate/"):
                    path = f"intermediate/{path}"
                artifacts.append(
                    DatasetArtifactRef(
                        path=_validate_repo_path(path, "file_path"),
                        media_type=self._media_type(
                            row.get("format") or row.get("media_type") or "json"
                        ),
                        size=self._integer(row.get("byte_size")),
                        sha256=row.get("sha256") or None,
                    )
                )

        for artifact in artifacts:
            self._verify_file(dataset_id, revision, artifact.path, kind="artifact")
        if context.layout == "legacy":
            for annotation in annotations:
                self._verify_file(dataset_id, revision, annotation.path, kind="reference")

        source_row = rows[0]
        metadata: dict[str, Any] = dict(source_row)
        contract_metadata = {
            "schema": DATASET_METADATA_SCHEMA,
            "provider_contract_version": DATASET_PROVIDER_CONTRACT_VERSION,
            "layout": context.layout,
            "reference_manifest_version": (
                REFERENCE_MANIFEST_SCHEMA_VERSION
                if context.layout != "legacy"
                else None
            ),
            "revision": {
                "requested": context.requested_revision,
                "effective": revision,
            },
            "references": bindings,
            "unannotated_tasks": unannotated_tasks,
            "manifest_records": [dict(row) for row in rows],
            "artifact_count_semantics": (
                "unique physical paths across artifacts and annotations; "
                "shared annotation files count once"
            ),
        }
        metadata["dataset_contract"] = contract_metadata
        return DatasetItem(
            id=item_id,
            dataset=dataset_id,
            split=split,
            revision=revision,
            artifacts=artifacts,
            annotations=annotations,
            metadata=metadata,
        )

    def _input_artifact(
        self, row: dict[str, str], dataset_id: str, revision: str
    ) -> DatasetArtifactRef:
        path = row.get("input_path") or row.get("file_path") or _legacy_input_path(row)
        if not path:
            raise DatasetProviderError(
                "input manifest row must provide input_path or original_filename",
                dataset=dataset_id,
                revision=revision,
                item=row.get("id", ""),
                reason="invalid_manifest",
            )
        path = _validate_repo_path(path, "input_path")
        if not path.startswith("input/"):
            raise DatasetProviderError(
                "input_path must point inside input/",
                dataset=dataset_id,
                revision=revision,
                item=row.get("id", ""),
                path=path,
                reason="invalid_manifest",
            )
        return DatasetArtifactRef(
            path=path,
            media_type=self._media_type(row.get("media_type") or row.get("format", "")),
            size=self._integer(row.get("byte_size")),
            sha256=row.get("sha256") or None,
        )

    def _reference_binding(self, row: dict[str, str]) -> dict[str, Any]:
        parsed_selector: dict[str, Any] | None = None
        if row["record_selector"]:
            parsed_selector = json.loads(row["record_selector"])
        return {
            "task": row["task"],
            "availability": row["availability"],
            "coverage_scope": row["coverage_scope"],
            "annotated_count": self._integer(row["annotated_count"]),
            "eligible_count": int(row["eligible_count"]) if row["eligible_count"] else None,
            "coverage_unit": row["coverage_unit"],
            "review_state": row["review_state"],
            "provenance": row["provenance"],
            "annotation_revision": row["annotation_revision"] or None,
            "reference_type": row["reference_type"],
            "format": row["format"],
            "schema_version": row["schema_version"],
            "content_schema": row["content_schema"] or None,
            "path": row["file_path"] or None,
            "record_selector": parsed_selector,
            "notes": row["notes"],
        }

    @staticmethod
    def _integer(value: str | None) -> int:
        if value is None or value == "":
            return 0
        try:
            return int(value)
        except ValueError:
            return 0

    @staticmethod
    def _artifact_count(item: DatasetItem) -> int:
        return len({ref.path for ref in [*item.artifacts, *item.annotations]})

    @staticmethod
    def _is_output_available(row: dict[str, str]) -> bool:
        status = (row.get("status") or "").strip().lower()
        return status in {"available", "complete", "completed", "present"}

    @staticmethod
    def _media_type(value: str) -> str:
        mapping = {
            "pdf": "application/pdf",
            "pdf_ua": "application/pdf",
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "png": "image/png",
            "tiff": "image/tiff",
            "csv": "text/csv",
            "json": "application/json",
            "txt": "text/plain",
            "html": "text/html",
            "mp3": "audio/mpeg",
            "epub": "application/epub+zip",
        }
        return mapping.get(value.lower(), value or "application/octet-stream")

    @staticmethod
    def _artifact_types_for_split(split: str) -> list[str]:
        mapping = {
            "input": ["application/pdf", "image/jpeg", "image/png"],
            "intermediate": ["application/json"],
            "outputs": [
                "text/plain",
                "text/html",
                "application/pdf",
                "audio/mpeg",
                "application/epub+zip",
            ],
        }
        return mapping.get(split, ["application/octet-stream"])


__all__ = [
    "DATASET_METADATA_SCHEMA",
    "DATASET_PROVIDER_CONTRACT_VERSION",
    "DATASET_REFERENCE_REVISION",
    "GitHubDatasetProvider",
]
