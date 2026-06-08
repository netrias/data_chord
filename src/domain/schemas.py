"""
Shared request/response schemas for cross-stage API contracts.

Used by multiple stages; stage-specific schemas belong in their respective packages.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer, WithJsonSchema

from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import (
    DATASET_WORKFLOW_ID_LENGTH,
    DATASET_WORKFLOW_ID_PATTERN,
    DatasetWorkflowId,
    dataset_workflow_id_from_value,
)
from src.domain.harmonize import HarmonizeStatus
from src.domain.manifest import ManifestPayload

FILE_ID_MIN_LENGTH = DATASET_WORKFLOW_ID_LENGTH

DatasetWorkflowIdField = Annotated[
    DatasetWorkflowId,
    BeforeValidator(dataset_workflow_id_from_value),
    PlainSerializer(str, return_type=str),
    Field(
        min_length=DATASET_WORKFLOW_ID_LENGTH,
        max_length=DATASET_WORKFLOW_ID_LENGTH,
        pattern=DATASET_WORKFLOW_ID_PATTERN,
    ),
    WithJsonSchema({
        "type": "string",
        "minLength": DATASET_WORKFLOW_ID_LENGTH,
        "maxLength": DATASET_WORKFLOW_ID_LENGTH,
        "pattern": DATASET_WORKFLOW_ID_PATTERN,
    }),
]

# Compatibility only. Public JSON fields may still be named file_id, but new
# schemas should spell the internal type as DatasetWorkflowIdField.
FileIdField = DatasetWorkflowIdField


class HarmonizeRequest(BaseModel):
    file_id: DatasetWorkflowIdField
    target_schema: str
    target_external_version_number: str = Field(..., min_length=1)
    manual_overrides: dict[str, str | None] = Field(default_factory=dict)
    column_renames: dict[str, str] = Field(default_factory=dict)
    manifest: ManifestPayload | None = None

    def data_model_version(self) -> DataModelVersionReference:
        return DataModelVersionReference(
            data_model_key=self.target_schema,
            external_version_number=self.target_external_version_number,
        )


class ConfidenceBucketSchema(BaseModel):
    id: str
    label: str
    term_count: int


class ColumnBreakdownSchema(BaseModel):
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
    elapsed_seconds: int | None = None
    manifest_summary: ManifestSummarySchema | None = None
