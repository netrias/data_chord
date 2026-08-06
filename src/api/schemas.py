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
from src.domain.harmonization import HarmonizationManifestSummary, HarmonizeStatus
from src.domain.manifest import ManifestPayload

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

class HarmonizeRequest(BaseModel):
    file_id: DatasetWorkflowIdField
    # Compatibility fields for callers that still seed legacy workflow state.
    # Current workflows resolve the model from durable WorkflowState, so a
    # retry only needs the workflow identity.
    data_model_key: str | None = Field(default=None, min_length=1)
    external_version_number: str | None = Field(default=None, min_length=1)
    manual_overrides: dict[str, str | None] = Field(default_factory=dict)
    column_renames: dict[str, str] = Field(default_factory=dict)
    manifest: ManifestPayload | None = None

    def data_model_version(self) -> DataModelVersionReference:
        if self.data_model_key is None or self.external_version_number is None:
            raise ValueError("A data model version is required to initialize legacy workflow state")
        return DataModelVersionReference(
            data_model_key=self.data_model_key,
            external_version_number=self.external_version_number,
        )


class HarmonizeResponse(BaseModel):
    job_id: str
    status: HarmonizeStatus
    detail: str
    next_stage_url: str
    job_id_available: bool = False
    elapsed_seconds: int | None = None
    manifest_summary: HarmonizationManifestSummary | None = None
