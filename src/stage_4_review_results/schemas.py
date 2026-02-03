"""
Data models for Stage 4 review overrides.

Define schemas for persisting human review edits and batch progress state.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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
                "column_mode": {},
                "row_mode": {
                    "current_unit": values.get("current_batch", 1),
                    "completed_units": values.get("completed_batches", []),
                    "flagged_units": values.get("flagged_batches", []),
                    "batch_size": values.get("batch_size", 5),
                },
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

    file_id: str
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


__all__ = [
    "CellOverrideSchema",
    "ReviewStateSchema",
    "ReviewOverridesSchema",
    "SaveOverridesRequest",
    "SaveOverridesResponse",
    "DeleteOverridesResponse",
]
