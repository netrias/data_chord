"""
Harmonization results including AI suggestions, confidence scores, and manual overrides.

Encapsulates manifest structure, reading, and writing; tracks override audit trail.
"""

from src.domain.manifest.models import (
    COMPLETENESS_HIGH_THRESHOLD,
    COMPLETENESS_MEDIUM_THRESHOLD,
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    ColumnMappingEntry,
    ConfidenceBucket,
    ManifestPayload,
    ManifestRow,
    ManifestSummary,
    ManualOverride,
    completeness_bucket,
    confidence_bucket,
    get_latest_override_value,
    get_manifest_schema,
    is_value_changed,
)
from src.domain.manifest.reader import read_manifest_parquet
from src.domain.manifest.writer import add_manual_overrides_batch

__all__ = [
    "COMPLETENESS_HIGH_THRESHOLD",
    "COMPLETENESS_MEDIUM_THRESHOLD",
    "ColumnMappingEntry",
    "ConfidenceBucket",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "ManifestPayload",
    "ManifestRow",
    "ManifestSummary",
    "ManualOverride",
    "add_manual_overrides_batch",
    "completeness_bucket",
    "confidence_bucket",
    "get_latest_override_value",
    "get_manifest_schema",
    "is_value_changed",
    "read_manifest_parquet",
]
