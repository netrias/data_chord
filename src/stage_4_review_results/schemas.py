"""Pydantic models for review overrides and batch progress persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


class CellOverrideSchema(BaseModel):
    """Single cell override from human review."""

    ai_value: str | None
    human_value: str
    original_value: str | None


class ReviewStateSchema(BaseModel):
    """Batch review progress state."""

    sort_mode: str = "original"
    batch_size: int = 5
    scroll_mode: bool = False


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

    file_id: str = Field(..., min_length=8, pattern=r"^[a-f0-9]+$")
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
