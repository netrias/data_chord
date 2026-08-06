"""Durable harmonization state shared by the workflow and API."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from src.domain.column_outcomes import FinalValueReviewStatus


class HarmonizeStatus(str, Enum):
    QUEUED = "queued"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ConfidenceBucketCount(BaseModel):
    id: str
    label: str
    term_count: int


class HarmonizationColumnBreakdown(BaseModel):
    column_name: str
    label: str
    column_key: str | None = None
    source_column_index: int | None = Field(default=None, ge=0)
    review_status: FinalValueReviewStatus | None = None
    total_rows: int
    changed_rows: int
    unchanged_rows: int
    unique_terms: int
    unique_terms_changed: int
    unique_terms_unchanged: int
    non_conformant_terms: int = 0
    confidence_buckets_changed: list[ConfidenceBucketCount]


class HarmonizationManifestSummary(BaseModel):
    total_terms: int
    changed_terms: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    non_conformant_terms: int = 0
    column_breakdowns: list[HarmonizationColumnBreakdown] = Field(default_factory=list)


__all__ = [
    "ConfidenceBucketCount",
    "HarmonizationColumnBreakdown",
    "HarmonizationManifestSummary",
    "HarmonizeStatus",
]
