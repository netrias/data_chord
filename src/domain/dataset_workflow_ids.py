"""Canonical identity for a dataset workflow rooted at an upload."""

from __future__ import annotations

import re
import secrets
import time
from typing import Final, NewType
from uuid import UUID

DATASET_WORKFLOW_ID_LENGTH: Final = 32
DATASET_WORKFLOW_ID_PATTERN: Final = r"^[a-f0-9]{32}$"
DATASET_WORKFLOW_ID_MIN_LENGTH: Final = DATASET_WORKFLOW_ID_LENGTH

DatasetWorkflowId = NewType("DatasetWorkflowId", str)

_DATASET_WORKFLOW_ID_RE: Final = re.compile(DATASET_WORKFLOW_ID_PATTERN)


def new_dataset_workflow_id() -> DatasetWorkflowId:
    """Create the app's stable dataset workflow identity as UUIDv7 hex."""
    unix_ts_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    uuid_int = (
        (unix_ts_ms & 0xFFFFFFFFFFFF) << 80
        | 0x7 << 76
        | rand_a << 64
        | 0b10 << 62
        | rand_b
    )
    return DatasetWorkflowId(UUID(int=uuid_int).hex)


def dataset_workflow_id_from_string(value: str) -> DatasetWorkflowId:
    """Convert a boundary string into a validated dataset workflow id."""
    if not _DATASET_WORKFLOW_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid dataset workflow id: {value}")
    return DatasetWorkflowId(value)


def dataset_workflow_id_from_value(value: object) -> DatasetWorkflowId:
    if not isinstance(value, str):
        raise ValueError(f"Invalid dataset workflow id: {value}")
    return dataset_workflow_id_from_string(value)


def is_dataset_workflow_id(value: object) -> bool:
    return isinstance(value, str) and _DATASET_WORKFLOW_ID_RE.fullmatch(value) is not None


__all__ = [
    "DATASET_WORKFLOW_ID_LENGTH",
    "DATASET_WORKFLOW_ID_MIN_LENGTH",
    "DATASET_WORKFLOW_ID_PATTERN",
    "DatasetWorkflowId",
    "dataset_workflow_id_from_string",
    "dataset_workflow_id_from_value",
    "is_dataset_workflow_id",
    "new_dataset_workflow_id",
]
