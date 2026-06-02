"""Compatibility aliases for the old external ``file_id`` identity name."""

from __future__ import annotations

from src.domain.dataset_workflow_ids import (
    DATASET_WORKFLOW_ID_LENGTH,
    DATASET_WORKFLOW_ID_MIN_LENGTH,
    DATASET_WORKFLOW_ID_PATTERN,
    DatasetWorkflowId,
    dataset_workflow_id_from_string,
    dataset_workflow_id_from_value,
    is_dataset_workflow_id,
    new_dataset_workflow_id,
)

FILE_ID_LENGTH = DATASET_WORKFLOW_ID_LENGTH
FILE_ID_PATTERN = DATASET_WORKFLOW_ID_PATTERN
FILE_ID_MIN_LENGTH = DATASET_WORKFLOW_ID_MIN_LENGTH

# Compatibility only. New domain code should use DatasetWorkflowId directly.
FileId = DatasetWorkflowId
new_file_id = new_dataset_workflow_id
file_id_from_string = dataset_workflow_id_from_string
file_id_from_value = dataset_workflow_id_from_value
is_file_id = is_dataset_workflow_id


__all__ = [
    "FILE_ID_LENGTH",
    "FILE_ID_MIN_LENGTH",
    "FILE_ID_PATTERN",
    "FileId",
    "file_id_from_string",
    "file_id_from_value",
    "is_file_id",
    "new_file_id",
]
