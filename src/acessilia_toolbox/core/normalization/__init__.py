"""Provider-agnostic normalization into the canonical structured document."""

from acessilia_toolbox.core.normalization.builder import build_processing_manifest
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.normalization.models import (
    SCHEMA_ID,
    SCHEMA_VERSION,
    ProcessingManifest,
)
from acessilia_toolbox.core.normalization.schema import (
    processing_manifest_schema,
    validate_manifest,
    write_processing_manifest_schema,
)

__all__ = [
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "ExtractionResult",
    "ProcessingManifest",
    "build_processing_manifest",
    "processing_manifest_schema",
    "validate_manifest",
    "write_processing_manifest_schema",
]
