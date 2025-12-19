"""
Data models for Stage 4 review overrides.

Define schemas for persisting human review edits and batch progress state.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CellOverrideSchema(BaseModel):
    """Single cell override from human review."""

    ai_value: str | None
    human_value: str
    original_value: str | None


class ReviewStateSchema(BaseModel):
    """Batch review progress state."""

    completed_batches: list[int] = Field(default_factory=list)  # why: avoid shared mutable defaults across instances
    flagged_batches: list[int] = Field(default_factory=list)  # why: avoid shared mutable defaults across instances
    current_batch: int = 1
    sort_mode: str = "original"
    batch_size: int = 5


class ReviewOverridesSchema(BaseModel):
    """Complete review overrides for a file."""

    file_id: str
    created_at: datetime
    updated_at: datetime
    overrides: dict[str, dict[str, CellOverrideSchema]]
    review_state: ReviewStateSchema


class SaveOverridesRequest(BaseModel):
    """Request payload for saving review overrides."""

    file_id: str = Field(..., min_length=8, pattern=r"^[a-f0-9]+$")
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
