"""
Harmonization manifest domain module.

Provides typed data models and I/O for harmonization manifest parquet files,
including manual override tracking with audit trail.
"""

from src.domain.manifest.models import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    ColumnMappingEntry,
    ConfidenceBucket,
    ManifestPayload,
    ManifestRow,
    ManifestSummary,
    ManualOverride,
    confidence_bucket,
    get_latest_override_value,
    get_manifest_schema,
    is_value_changed,
)
from src.domain.manifest.reader import read_manifest_parquet
from src.domain.manifest.writer import (
    add_manual_override,
    add_manual_overrides_batch,
)

__all__ = [
    "ColumnMappingEntry",
    "ConfidenceBucket",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "ManifestPayload",
    "ManifestRow",
    "ManifestSummary",
    "ManualOverride",
    "add_manual_override",
    "add_manual_overrides_batch",
    "confidence_bucket",
    "get_latest_override_value",
    "get_manifest_schema",
    "is_value_changed",
    "read_manifest_parquet",
]
