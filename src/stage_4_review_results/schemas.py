"""Pydantic models for review overrides and batch progress persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.api.schemas import DatasetWorkflowIdField
from src.domain.harmonization import MatchFidelity


class CellOverrideSchema(BaseModel):
    """Single cell override from human review."""

    model_config = ConfigDict(extra="forbid")

    human_value: Annotated[str, Field(min_length=1)]
    original_value: str


class ReviewModeStateSchema(BaseModel):
    """Review progress state for a single mode (column or row)."""

    model_config = ConfigDict(extra="forbid")

    current_unit: Annotated[int, Field(ge=1)] = 1
    batch_size: Annotated[int, Field(ge=1)] = 5


class ReviewStateSchema(BaseModel):
    """Review progress state across column and row modes."""

    model_config = ConfigDict(extra="forbid")

    review_mode: Literal["column", "row"] = "column"
    sort_mode: Literal["original", "fidelity-asc", "fidelity-desc"] = "original"
    scroll_mode: bool = False
    show_case_only_changes: bool = False
    show_unchanged_values: bool = False
    column_mode: ReviewModeStateSchema = Field(default_factory=ReviewModeStateSchema)
    row_mode: ReviewModeStateSchema = Field(default_factory=ReviewModeStateSchema)


class ReviewOverridesSchema(BaseModel):
    """Complete review overrides for a file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3]
    file_id: DatasetWorkflowIdField
    created_at: datetime
    updated_at: datetime
    overrides: dict[str, dict[str, CellOverrideSchema]]
    review_state: ReviewStateSchema


class SaveOverridesRequest(BaseModel):
    """Request payload for saving review overrides."""

    model_config = ConfigDict(extra="forbid")

    file_id: DatasetWorkflowIdField
    overrides: dict[str, dict[str, CellOverrideSchema]]
    review_state: ReviewStateSchema


class SaveOverridesResponse(BaseModel):
    """Response after saving review overrides."""

    file_id: DatasetWorkflowIdField
    updated_at: datetime


class StageFourResultsRequest(BaseModel):
    """Request payload for loading Stage 4 harmonized review rows."""

    file_id: DatasetWorkflowIdField


class NonConformantItem(BaseModel):
    """A single non-conformant value for the gating dialog."""

    column: str
    value: str
    original: str


class NonConformantResponse(BaseModel):
    """Response containing non-conformant value count and samples."""

    count: int
    items: list[NonConformantItem]


class RowContextRequest(BaseModel):
    """Request payload for fetching original row context."""

    file_id: DatasetWorkflowIdField
    row_indices: list[Annotated[int, Field(ge=0)]] = Field(max_length=10000)


class RowContextResponse(BaseModel):
    """Original spreadsheet rows with all columns for context display."""

    headers: list[str]
    rows: list[list[str]]


class SuggestionInfo(BaseModel):
    """AI suggestion with PV conformance flag for dropdown display."""

    value: str
    isPVConformant: bool


class Transformation(BaseModel):
    """A unique original→harmonized mapping with affected row indices."""

    originalValue: str
    harmonizedValue: str | None
    matchFidelity: MatchFidelity
    isChanged: bool
    recommendationType: str  # RecommendationType.value for JSON serialization
    isPVConformant: bool = Field(
        description="Whether the initial server review value is permissible."
    )
    pvSetAvailable: bool
    topSuggestions: list[SuggestionInfo]
    rowIndices: list[int]  # 1-based source row indices
    manualOverride: str | None = None


class ColumnReviewData(BaseModel):
    """All transformations for a single harmonized column."""

    columnKey: str
    columnLabel: str
    targetCdeKey: str | None = None
    targetCdeLabel: str | None = None
    sourceColumnIndex: int
    termCount: int
    termsWithChanges: int
    transformations: list[Transformation]


class StageFourResultsResponse(BaseModel):
    """Column-centric response for Stage 4 review UI."""

    columns: list[ColumnReviewData]
    columnPVs: dict[str, list[str]] = {}
    totalOriginalRows: int = 0


__all__ = [
    "CellOverrideSchema",
    "ColumnReviewData",
    "NonConformantItem",
    "NonConformantResponse",
    "ReviewOverridesSchema",
    "ReviewStateSchema",
    "RowContextRequest",
    "RowContextResponse",
    "SaveOverridesRequest",
    "SaveOverridesResponse",
    "StageFourResultsRequest",
    "StageFourResultsResponse",
    "SuggestionInfo",
    "Transformation",
]
