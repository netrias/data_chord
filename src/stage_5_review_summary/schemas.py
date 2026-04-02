"""Pydantic models for Stage 5 summary and download API responses."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN


class StageFiveRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)


class ColumnSummary(BaseModel):
    column: str
    distinct_terms: int
    ai_changes: int
    manual_changes: int
    unchanged: int


class TransformationStep(BaseModel):
    value: str
    source: str
    timestamp: str | None = None
    user_id: str | None = None
    is_pv_conformant: bool = True


class TermMapping(BaseModel):
    column: str
    original_value: str
    final_value: str
    is_pv_conformant: bool = True
    history: list[TransformationStep] = Field(default_factory=list)


class StageFiveSummaryResponse(BaseModel):
    column_summaries: list[ColumnSummary]
    term_mappings: list[TermMapping]
    non_conformant_count: int = 0


__all__ = [
    "ColumnSummary",
    "StageFiveRequest",
    "StageFiveSummaryResponse",
    "TermMapping",
    "TransformationStep",
]
