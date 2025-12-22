"""Describe request and response models for the upload stage."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.domain import DEFAULT_TARGET_SCHEMA, ModelSuggestion
from src.domain.manifest import ConfidenceBucket, ManifestPayload


class UploadResponse(BaseModel):
    """why: capture the metadata the UI needs after a file upload."""

    file_id: str = Field(..., min_length=8, max_length=128)
    file_name: str
    human_size: str
    content_type: str
    uploaded_at: datetime


class AnalyzeRequest(BaseModel):
    """why: indicate which file should be profiled for column insight."""

    file_id: str = Field(..., min_length=8, max_length=128, pattern=r"^[a-f0-9]+$")
    target_schema: str = Field(default=DEFAULT_TARGET_SCHEMA, min_length=1)


class ColumnPreview(BaseModel):
    """why: present a concise summary of each detected column."""

    column_name: str
    inferred_type: str
    sample_values: list[str]
    confidence_bucket: ConfidenceBucket
    confidence_score: float = Field(ge=0.0, le=1.0)


class AnalyzeResponse(BaseModel):
    """why: return the information needed to tee up stage two."""

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
