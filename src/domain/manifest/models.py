"""
Define harmonization manifest data models.

Core dataclasses representing the harmonization manifest parquet schema,
including manual override tracking with audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

import pyarrow as pa

ConfidenceBucket = Literal["low", "medium", "high"]

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
        return "low"
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


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
    "get_manifest_schema",
]
