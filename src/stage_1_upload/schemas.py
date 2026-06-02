"""Pydantic models for upload metadata and column preview responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.domain import ModelSuggestion
from src.domain.column_profile import ColumnProfilePayload
from src.domain.manifest import ConfidenceBucket, ManifestPayload
from src.domain.schemas import DatasetWorkflowIdField


class SheetPreview(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    truncated_rows: bool = False
    truncated_columns: bool = False


class UploadResponse(BaseModel):
    file_id: DatasetWorkflowIdField
    file_name: str
    human_size: str
    content_type: str
    uploaded_at: datetime
    tabular_format: str
    sheet_names: list[str] = Field(default_factory=list)
    selected_sheet: str | None = None
    sheet_previews: dict[str, SheetPreview] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    file_id: DatasetWorkflowIdField
    target_schema: str = Field(..., min_length=1)
    target_version_number: int | None = Field(default=None, ge=1)
    sheet_name: str | None = None


class ColumnPreview(BaseModel):
    column_name: str
    column_key: str
    source_index: int
    header: str
    inferred_type: str
    sample_values: list[str]
    confidence_bucket: ConfidenceBucket
    confidence_score: float = Field(ge=0.0, le=1.0)


class ColumnOverlapRatio(BaseModel):
    """Precomputed AI-rec PV overlap; None means the ratio is undefined."""

    value_overlap_ratio: float | None = Field(default=None, ge=0.0, le=1.0)


class AnalyzeResponse(BaseModel):
    """Stage 1 analyze response.

    ``column_profiles`` stays optional for older browser sessions. New Stage 2
    detail calls load one selected column profile at a time so analyze responses
    do not carry every distinct value for every column.
    """

    file_id: DatasetWorkflowIdField
    file_name: str
    target_version_number: int | None = None
    total_rows: int = Field(ge=0)
    columns: list[ColumnPreview]
    column_profiles: dict[str, ColumnProfilePayload] = Field(default_factory=dict)
    column_summaries: dict[str, ColumnOverlapRatio] = Field(default_factory=dict)
    cde_targets: dict[str, list[ModelSuggestion]]
    next_stage: str
    next_step_hint: str
    manual_overrides: dict[str, str] = Field(default_factory=dict)
    manifest: ManifestPayload = Field(default_factory=lambda: {"column_mappings": {}})
