"""Durable harmonization state shared by the workflow and API."""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic import BaseModel, Field

from src.domain.column_outcomes import FinalValueReviewStatus


class HarmonizeStatus(str, Enum):
    QUEUED = "queued"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MatchFidelity(StrEnum):
    STRONG = "strong"
    PARTIAL = "partial"
    APPROXIMATE = "approximate"
    NONE = "none"

    @property
    def label(self) -> str:
        return self.value.title()


class MatchFidelityCount(BaseModel):
    id: MatchFidelity
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
    successfully_harmonized_terms: int | None = None
    unique_terms_unchanged: int
    non_conformant_terms: int = 0
    match_fidelity_counts_changed: list[MatchFidelityCount]


class HarmonizationManifestSummary(BaseModel):
    total_terms: int
    changed_terms: int
    match_fidelity_counts: list[MatchFidelityCount]
    non_conformant_terms: int = 0
    source_file_name: str | None = None
    reference_model_label: str | None = None
    reference_model_version: str | None = None
    column_breakdowns: list[HarmonizationColumnBreakdown] = Field(default_factory=list)


__all__ = [
    "MatchFidelity",
    "MatchFidelityCount",
    "HarmonizationColumnBreakdown",
    "HarmonizationManifestSummary",
    "HarmonizeStatus",
]
