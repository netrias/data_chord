"""Pydantic models for upload metadata and column preview responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.domain import ModelSuggestion, get_default_target_schema
from src.domain.manifest import ConfidenceBucket, ManifestPayload
from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN


class UploadResponse(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, max_length=128)
    file_name: str
    human_size: str
    content_type: str
    uploaded_at: datetime


class AnalyzeRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, max_length=128, pattern=FILE_ID_PATTERN)
    target_schema: str = Field(default_factory=get_default_target_schema, min_length=1)


class ColumnPreview(BaseModel):
    column_name: str
    inferred_type: str
    sample_values: list[str]
    confidence_bucket: ConfidenceBucket
    confidence_score: float = Field(ge=0.0, le=1.0)


class AnalyzeResponse(BaseModel):
    file_id: str
    file_name: str
    total_rows: int = Field(ge=0)
    columns: list[ColumnPreview]
    cde_targets: dict[str, list[ModelSuggestion]]
    next_stage: str
    next_step_hint: str
    manual_overrides: dict[str, str] = Field(default_factory=dict)
    manifest: ManifestPayload = Field(default_factory=lambda: {"column_mappings": {}})
    mapping_service_available: bool = Field(default=True)
