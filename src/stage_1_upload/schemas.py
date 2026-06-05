"""Pydantic models for upload metadata and column preview responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from src.domain import ModelSuggestion
from src.domain.column_profile import ColumnProfilePayload
from src.domain.data_model_selection import DataModelSelection
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
    target_external_version_number: str | None = Field(default=None, min_length=1)
    target_version_number: int | None = Field(default=None, ge=1)
    sheet_name: str | None = None

    @model_validator(mode="after")
    def _require_version(self) -> AnalyzeRequest:
        if self.target_external_version_number is None and self.target_version_number is None:
            raise ValueError("target_external_version_number is required")
        if self.target_external_version_number is not None:
            DataModelSelection.from_external_version_number(
                self.target_schema,
                self.target_external_version_number,
            )
        return self

    def data_model_selection(self) -> DataModelSelection:
        if self.target_external_version_number is not None:
            return DataModelSelection.from_external_version_number(
                self.target_schema,
                self.target_external_version_number,
            )
        if self.target_version_number is None:
            raise ValueError("target_external_version_number is required")
        return DataModelSelection.from_legacy_version_number(self.target_schema, self.target_version_number)


class ColumnPreview(BaseModel):
    column_name: str
    column_key: str
    source_index: int
    header: str
    inferred_type: str
    sample_values: list[str]
    has_non_empty_values: bool = Field(
        description="True when the full uploaded column has at least one non-empty value.",
    )
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
    target_external_version_number: str
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
