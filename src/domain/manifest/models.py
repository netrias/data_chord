"""
Define harmonization manifest data models.

Core dataclasses representing the harmonization manifest parquet schema,
including manual override tracking with audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypedDict

import pyarrow as pa


class ConfidenceBucket(str, Enum):
    """why: classify confidence scores into discrete UI-friendly buckets."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def label(self) -> str:
        """why: provide human-readable label for UI display."""
        return self.value.title()


HIGH_CONFIDENCE_THRESHOLD: float = 0.8
MEDIUM_CONFIDENCE_THRESHOLD: float = 0.45


class ColumnMappingEntry(TypedDict, total=False):
    """why: describe a single column's CDE mapping configuration.

    Fields:
        route: The routing endpoint for harmonization (e.g., "sagemaker:primary")
        targetField: The canonical CDE field name
        cde_id: The numeric CDE identifier
    """

    route: str
    targetField: str
    cde_id: int


class ManifestPayload(TypedDict, total=False):
    """why: structure for CDE mapping payloads passed to harmonization.

    The column_mappings dict maps source column names to their CDE configurations.
    Uses total=False to allow flexible construction of the dict.
    """

    column_mappings: dict[str, dict[str, object]]


@dataclass(frozen=True)
class ManualOverride:
    """why: track user edits to harmonization values with audit trail."""

    user_id: str | None
    timestamp: str
    value: str


@dataclass(frozen=True)
class ManifestRow:
    """why: represent a single row from the harmonization manifest parquet."""

    job_id: str
    column_id: int
    column_name: str
    to_harmonize: str
    top_harmonization: str
    ontology_id: str | None
    top_harmonizations: list[str]
    confidence_score: float | None
    error: str | None
    row_indices: list[int]
    manual_overrides: list[ManualOverride]


@dataclass(frozen=True)
class ManifestSummary:
    """why: aggregate manifest data for frontend consumption."""

    total_terms: int
    changed_terms: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    rows: list[ManifestRow]


def confidence_bucket(score: float | None) -> ConfidenceBucket:
    """why: classify confidence scores into UI-friendly buckets."""
    if score is None:
        return ConfidenceBucket.LOW
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceBucket.HIGH
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return ConfidenceBucket.MEDIUM
    return ConfidenceBucket.LOW


def is_value_changed(original: str | None, harmonized: str | None) -> bool:
    """why: determine if harmonization produced a meaningfully different value.

    Canonical change detection used across all stages. Returns False if:
    - harmonized is empty/None (no recommendation made)
    - normalized values are identical (case-insensitive, trimmed)
    """
    original_normalized = (original or "").strip().lower()
    harmonized_normalized = (harmonized or "").strip().lower()
    if not harmonized_normalized:
        return False
    return original_normalized != harmonized_normalized


def get_latest_override_value(overrides: list[ManualOverride]) -> str | None:
    """why: extract the most recent manual override value, if any."""
    if not overrides:
        return None
    return overrides[-1].value


def get_manifest_schema() -> pa.Schema:
    """why: define the canonical parquet schema for manifest files."""
    override_struct = pa.struct([
        ("user_id", pa.string()),
        ("timestamp", pa.string()),
        ("value", pa.string()),
    ])

    return pa.schema([
        ("job_id", pa.string()),
        ("column_id", pa.int64()),
        ("column_name", pa.string()),
        ("to_harmonize", pa.string()),
        ("top_harmonization", pa.string()),
        ("ontology_id", pa.string()),
        ("top_harmonizations", pa.list_(pa.string())),
        ("confidence_score", pa.float64()),
        ("error", pa.string()),
        ("row_indices", pa.list_(pa.int64())),
        ("manual_overrides", pa.list_(override_struct)),
    ])


__all__ = [
    "ColumnMappingEntry",
    "ConfidenceBucket",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "ManifestPayload",
    "ManifestRow",
    "ManifestSummary",
    "ManualOverride",
    "confidence_bucket",
    "get_latest_override_value",
    "get_manifest_schema",
    "is_value_changed",
]
