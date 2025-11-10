"""Describe request and response models for the upload stage."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ConfidenceBucket = Literal["low", "medium", "high"]
DEFAULT_TARGET_SCHEMA = "ccdi"


class UploadResponse(BaseModel):
    """why: capture the metadata the UI needs after a file upload."""

    file_id: str = Field(..., min_length=8)
    file_name: str
    human_size: str
    content_type: str
    uploaded_at: datetime


class AnalyzeRequest(BaseModel):
    """why: indicate which file should be profiled for column insight."""

    file_id: str = Field(..., min_length=8, pattern=r"^[a-f0-9]+$")
    target_schema: str = Field(default=DEFAULT_TARGET_SCHEMA, min_length=1)


class ColumnPreview(BaseModel):
    """why: present a concise summary of each detected column."""

    column_name: str
    inferred_type: str
    sample_values: list[str]
    confidence_bucket: ConfidenceBucket
    confidence_score: float = Field(ge=0.0, le=1.0)


class ModelSuggestion(BaseModel):
    """why: surface an individual CDE recommendation for the UI."""

    target: str
    similarity: float


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


class HarmonizeRequest(BaseModel):
    """why: capture data required to kick off harmonization."""

    file_id: str = Field(..., min_length=8, pattern=r"^[a-f0-9]+$")
    target_schema: str
    manual_overrides: dict[str, str] = Field(default_factory=dict)


class HarmonizeResponse(BaseModel):
    """why: provide the job metadata to the UI."""

    job_id: str
    status: str
    detail: str
    next_stage_url: str
