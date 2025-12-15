"""
Harmonization manifest domain module.

Provides typed data models and I/O for harmonization manifest parquet files,
including manual override tracking with audit trail.
"""

from src.domain.manifest.models import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    ConfidenceBucket,
    ManifestRow,
    ManifestSummary,
    ManualOverride,
    confidence_bucket,
)
from src.domain.manifest.reader import read_manifest_parquet
from src.domain.manifest.writer import add_manual_override

__all__ = [
    "ConfidenceBucket",
    "ManualOverride",
    "ManifestRow",
    "ManifestSummary",
    "confidence_bucket",
    "read_manifest_parquet",
    "add_manual_override",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
]
