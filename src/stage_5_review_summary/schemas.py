"""Pydantic models for Stage 5 summary and download requests."""

from __future__ import annotations

from pydantic import BaseModel

from src.api.schemas import DatasetWorkflowIdField


class StageFiveRequest(BaseModel):
    file_id: DatasetWorkflowIdField


class ColumnSummary(BaseModel):
    column: str
    distinct_terms: int
    ai_changes: int
    manual_changes: int
    unchanged: int


class TransformationStep(BaseModel):
    value: str
    source: str  # "original", "ai", "user"
    timestamp: str | None = None
    user_id: str | None = None
    is_pv_conformant: bool = True


class TermMapping(BaseModel):
    column: str
    original_value: str
    final_value: str
    is_pv_conformant: bool = True
    history: list[TransformationStep] = []


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
