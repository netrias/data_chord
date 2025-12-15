"""
Define harmonization manifest data models.

Core dataclasses representing the harmonization manifest parquet schema,
including manual override tracking with audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ConfidenceBucket = Literal["low", "medium", "high"]

HIGH_CONFIDENCE_THRESHOLD: float = 0.8
MEDIUM_CONFIDENCE_THRESHOLD: float = 0.45


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


__all__ = [
    "ConfidenceBucket",
    "ManualOverride",
    "ManifestRow",
    "ManifestSummary",
    "confidence_bucket",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
]
