"""
Shared request/response schemas for cross-stage API contracts.

Used by multiple stages; stage-specific schemas belong in their respective packages.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.cde import ColumnMappingDecision
from src.domain.harmonize import HarmonizeStatus
from src.domain.manifest import ManifestPayload

FILE_ID_PATTERN = r"^[a-f0-9]+$"
FILE_ID_MIN_LENGTH = 8


class HarmonizeRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)
    target_schema: str
    manual_overrides: dict[int, str] = Field(default_factory=dict)
    manifest: ManifestPayload | None = None
    mapping_decisions: list[ColumnMappingDecision] = Field(default_factory=list)


class ConfidenceBucketSchema(BaseModel):
    id: str
    label: str
    term_count: int


class ColumnBreakdownSchema(BaseModel):
    column_id: int
    column_name: str
    label: str
    total_rows: int
    changed_rows: int
    unchanged_rows: int
    unique_terms: int
    unique_terms_changed: int
    unique_terms_unchanged: int
    non_conformant_terms: int = 0
    confidence_buckets_changed: list[ConfidenceBucketSchema]


class ManifestSummarySchema(BaseModel):
    total_terms: int
    changed_terms: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    non_conformant_terms: int = 0
    column_breakdowns: list[ColumnBreakdownSchema] = Field(default_factory=list)


class HarmonizeResponse(BaseModel):
    job_id: str
    status: HarmonizeStatus
    detail: str
    next_stage_url: str
    job_id_available: bool = False
    manifest_summary: ManifestSummarySchema | None = None
