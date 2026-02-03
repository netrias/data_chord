"""Pydantic models for review overrides and batch progress persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN


class CellOverrideSchema(BaseModel):
    """Single cell override from human review."""

    ai_value: str | None
    human_value: str
    original_value: str | None


class ReviewModeStateSchema(BaseModel):
    """Review progress state for a single mode (column or row)."""

    current_unit: int = 1
    completed_units: list[int] = Field(default_factory=list)
    flagged_units: list[int] = Field(default_factory=list)
    batch_size: int = 5


class ReviewStateSchema(BaseModel):
    """Review progress state across column and row modes."""

    review_mode: str = "column"
    sort_mode: str = "original"
    scroll_mode: bool = False
    column_mode: ReviewModeStateSchema = Field(default_factory=ReviewModeStateSchema)
    row_mode: ReviewModeStateSchema = Field(default_factory=ReviewModeStateSchema)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        if {"review_mode", "column_mode", "row_mode"} & values.keys():
            return values
        if {"completed_batches", "flagged_batches", "current_batch", "batch_size"} & values.keys():
            return {
                "review_mode": "row",
                "sort_mode": values.get("sort_mode", "original"),
                "scroll_mode": values.get("scroll_mode", False),
                "column_mode": {},
                "row_mode": {
                    "current_unit": values.get("current_batch", 1),
                    "completed_units": values.get("completed_batches", []),
                    "flagged_units": values.get("flagged_batches", []),
                    "batch_size": values.get("batch_size", 5),
                },
            }
        if {"batch_size", "sort_mode", "scroll_mode"} & values.keys():
            batch_size = values.get("batch_size", 5)
            return {
                "review_mode": values.get("review_mode", "column"),
                "sort_mode": values.get("sort_mode", "original"),
                "scroll_mode": values.get("scroll_mode", False),
                "column_mode": {"batch_size": batch_size},
                "row_mode": {"batch_size": batch_size},
            }
        return values


class ReviewOverridesSchema(BaseModel):
    """Complete review overrides for a file."""

    file_id: str
    created_at: datetime
    updated_at: datetime
    overrides: dict[str, dict[str, CellOverrideSchema]]
    review_state: ReviewStateSchema


class SaveOverridesRequest(BaseModel):
    """Request payload for saving review overrides."""

    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)
    overrides: dict[str, dict[str, CellOverrideSchema]]
    review_state: ReviewStateSchema


class SaveOverridesResponse(BaseModel):
    """Response after saving review overrides."""

    file_id: str
    updated_at: datetime


class DeleteOverridesResponse(BaseModel):
    """Response after deleting review overrides."""

    file_id: str
    deleted: bool


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

    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)
    row_indices: list[Annotated[int, Field(ge=0)]] = Field(max_length=10000)


class RowContextResponse(BaseModel):
    """Original spreadsheet rows with all columns for context display."""

    headers: list[str]
    rows: list[list[str]]


__all__ = [
    "CellOverrideSchema",
    "DeleteOverridesResponse",
    "NonConformantItem",
    "NonConformantResponse",
    "ReviewOverridesSchema",
    "ReviewStateSchema",
    "RowContextRequest",
    "RowContextResponse",
    "SaveOverridesRequest",
    "SaveOverridesResponse",
]
