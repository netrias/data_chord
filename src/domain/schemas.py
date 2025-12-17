"""
Shared HTTP request/response schemas for cross-stage API contracts.

These schemas define the API boundary between stages and are used by multiple
stages for harmonization workflows.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.manifest import ManifestPayload


class HarmonizeRequest(BaseModel):
    """why: capture data required to kick off harmonization."""

    file_id: str = Field(..., min_length=8, pattern=r"^[a-f0-9]+$")
    target_schema: str
    manual_overrides: dict[str, str] = Field(default_factory=dict)
    manifest: ManifestPayload | None = None


class ManifestRowSchema(BaseModel):
    """why: API representation of a manifest row for frontend consumption."""

    column_name: str
    to_harmonize: str
    top_harmonization: str
    confidence_score: float | None
    row_indices: list[int] = Field(default_factory=list)


class ManifestSummarySchema(BaseModel):
    """why: aggregate manifest metrics for dashboard widgets."""

    total_terms: int
    changed_terms: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    preview_rows: list[ManifestRowSchema] = Field(default_factory=list)


class HarmonizeResponse(BaseModel):
    """why: provide the job metadata to the UI."""

    job_id: str
    status: str
    detail: str
    next_stage_url: str
    job_id_available: bool = False
    manifest_summary: ManifestSummarySchema | None = None
