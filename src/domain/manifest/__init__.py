"""
Harmonization results including AI suggestions and match fidelity.

Encapsulates manifest structure, reading, and writing.
"""

from src.domain.manifest.adjustments import ManifestPvAdjustment, ManifestTermKey
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
    InvalidMappingManifestError,
    MappingAlternative,
    RecommendationSource,
    normalize_manifest,
)
from src.domain.manifest.models import (
    COMPLETENESS_HIGH_THRESHOLD,
    COMPLETENESS_MEDIUM_THRESHOLD,
    ColumnMappingEntry,
    ConfidenceBucket,
    ManifestPayload,
    ManifestRow,
    ManifestSummary,
    completeness_bucket,
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
    "InvalidMappingManifestError",
    "ConfidenceBucket",
    "DEFAULT_HARMONIZATION",
    "MANIFEST_FIELD_COLUMN_MAPPINGS",
    "MAPPING_FIELD_ALTERNATIVES",
    "MAPPING_FIELD_CDE_ID",
    "MAPPING_FIELD_CDE_KEY",
    "MAPPING_FIELD_COLUMN_NAME",
    "MAPPING_FIELD_CONFIDENCE",
    "MAPPING_FIELD_HARMONIZATION",
    "MAPPING_FIELD_ROUTE",
    "MAPPING_FIELD_TARGET",
    "ManifestPayload",
    "ManifestPvAdjustment",
    "ManifestRow",
    "ManifestSummary",
    "ManifestTermKey",
    "MappingAlternative",
    "RecommendationSource",
    "completeness_bucket",
    "is_value_changed",
    "normalize_manifest",
]
