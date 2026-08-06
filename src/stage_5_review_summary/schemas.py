"""Pydantic models for Stage 5 summary and download requests."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.api.schemas import DatasetWorkflowIdField
from src.domain.column_outcomes import FinalValueReviewStatus, FinalValueSource


class StageFiveRequest(BaseModel):
    file_id: DatasetWorkflowIdField


class ColumnSummary(BaseModel):
    column: str
    column_key: str
    source_column_index: int
    distinct_terms: int
    changed_distinct_values: int
    total_rows: int
    changed_rows: int
    reviewer_edited_rows: int
    non_conformant_values: int
    review_status: FinalValueReviewStatus
    ai_changes: int
    manual_changes: int
    unchanged: int


class DatasetSummary(BaseModel):
    """Display metadata for the dataset and selected model version."""

    filename: str | None = None
    tabular_format: str | None = None
    data_model_key: str
    external_version_number: str


class TransformationStep(BaseModel):
    value: str
    source: str  # "original", "ai", "user"
    timestamp: str | None = None
    user_id: str | None = None
    review_status: FinalValueReviewStatus = FinalValueReviewStatus.NOT_CHECKED


class TermMapping(BaseModel):
    column: str
    column_key: str
    source_column_index: int
    original_value: str
    final_value: str
    is_changed: bool
    final_value_source: FinalValueSource
    review_status: FinalValueReviewStatus
    row_count: int
    history: list[TransformationStep] = Field(default_factory=list)


class StageFiveSummaryResponse(BaseModel):
    dataset: DatasetSummary
    column_summaries: list[ColumnSummary]
    term_mappings: list[TermMapping]
    non_conformant_count: int = 0


__all__ = [
    "ColumnSummary",
    "DatasetSummary",
    "StageFiveRequest",
    "StageFiveSummaryResponse",
    "TermMapping",
    "TransformationStep",
]
