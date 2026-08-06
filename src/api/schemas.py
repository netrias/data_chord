"""
Shared request/response schemas for cross-stage API contracts.

Used by multiple stages; stage-specific schemas belong in their respective packages.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, WithJsonSchema

from src.domain.dataset_workflow_ids import (
    DATASET_WORKFLOW_ID_LENGTH,
    DATASET_WORKFLOW_ID_PATTERN,
    DatasetWorkflowId,
    dataset_workflow_id_from_value,
)
from src.domain.harmonization import HarmonizationManifestSummary, HarmonizeStatus

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
    model_config = ConfigDict(extra="forbid")

    file_id: DatasetWorkflowIdField


class HarmonizeResponse(BaseModel):
    job_id: str
    status: HarmonizeStatus
    detail: str
    next_stage_url: str
    job_id_available: bool = False
    elapsed_seconds: int | None = None
    manifest_summary: HarmonizationManifestSummary | None = None
