"""
Harmonization results including AI suggestions, confidence scores, and manual overrides.

Encapsulates manifest structure, reading, and writing; tracks override audit trail.
"""

from src.domain.manifest.adjustments import ManifestManualOverride, ManifestPvAdjustment, ManifestTermKey
from src.domain.manifest.mapping_manifest import (
    DEFAULT_HARMONIZATION,
    MANIFEST_FIELD_COLUMN_MAPPINGS,
    MAPPING_FIELD_ALTERNATIVES,
    MAPPING_FIELD_CDE_ID,
    MAPPING_FIELD_CDE_KEY,
    MAPPING_FIELD_COLUMN_NAME,
    MAPPING_FIELD_CONFIDENCE,
    MAPPING_FIELD_HARMONIZATION,
    MAPPING_FIELD_ROUTE,
    MAPPING_FIELD_TARGET,
    ColumnMappingManifest,
    ColumnMappingRecord,
    MappingAlternative,
    normalize_manifest,
)
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
from src.domain.manifest.models import (
    AlternativeEntry as AlternativeEntry,
)

__all__ = [
    "AlternativeEntry",
    "COMPLETENESS_HIGH_THRESHOLD",
    "COMPLETENESS_MEDIUM_THRESHOLD",
    "ColumnMappingEntry",
    "ColumnMappingManifest",
    "ColumnMappingRecord",
    "ConfidenceBucket",
    "DEFAULT_HARMONIZATION",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MANIFEST_FIELD_COLUMN_MAPPINGS",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "MAPPING_FIELD_ALTERNATIVES",
    "MAPPING_FIELD_CDE_ID",
    "MAPPING_FIELD_CDE_KEY",
    "MAPPING_FIELD_COLUMN_NAME",
    "MAPPING_FIELD_CONFIDENCE",
    "MAPPING_FIELD_HARMONIZATION",
    "MAPPING_FIELD_ROUTE",
    "MAPPING_FIELD_TARGET",
    "ManifestPayload",
    "ManifestManualOverride",
    "ManifestPvAdjustment",
    "ManifestRow",
    "ManifestSummary",
    "ManifestTermKey",
    "ManualOverride",
    "MappingAlternative",
    "completeness_bucket",
    "confidence_bucket",
    "get_latest_override_value",
    "get_manifest_schema",
    "is_value_changed",
    "normalize_manifest",
]
