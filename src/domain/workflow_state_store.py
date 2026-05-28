"""Persist WorkflowState through the typed workflow storage boundary.

Axis of change: how the backend stores and updates durable workflow progress.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.storage import (
    UserContext,
    WorkflowConflictError,
    WorkflowFile,
    WorkflowNotFoundError,
    WorkflowStorage,
)
from src.domain.workflow_state import ConfirmedMappingChoices, WorkflowState


class WorkflowStateNotFoundError(Exception):
    """Raised when a workflow has no stored state yet."""


class WorkflowStateConflictError(Exception):
    """Raised when workflow state changed during a read-modify-write update."""


def create_workflow_record(storage: WorkflowStorage, user: UserContext, file_id: str) -> None:
    """Create owner metadata for a newly uploaded workflow."""
    storage.create_workflow(user, file_id=file_id)


def save_initial_workflow_state(
    storage: WorkflowStorage,
    user: UserContext,
    state: WorkflowState,
) -> None:
    """Create or replace the workflow selection while preserving version checks."""
    try:
        existing = storage.read_json(user, state.file_id, WorkflowFile.WORKFLOW_STATE)
    except WorkflowNotFoundError:
        # Some callers can arrive with artifacts restored from older local-only
        # flows, so create the owner record here instead of failing late.
        storage.create_workflow(user, file_id=state.file_id)
        existing = None
    expected_version = existing.version if existing is not None else None
    try:
        storage.write_json(
            user,
            state.file_id,
            WorkflowFile.WORKFLOW_STATE,
            state.to_store(),
            expected_version=expected_version,
        )
    except WorkflowConflictError as exc:
        raise WorkflowStateConflictError(state.file_id) from exc


def load_workflow_state(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> WorkflowState | None:
    try:
        stored = storage.read_json(user, file_id, WorkflowFile.WORKFLOW_STATE)
    except WorkflowNotFoundError:
        return None
    if stored is None:
        return None
    return WorkflowState.from_store(stored.data, file_id)


def save_confirmed_mapping_choices_to_state(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    manual_overrides: Mapping[str, str | None],
    column_renames: Mapping[str, str],
) -> WorkflowState:
    try:
        stored = storage.read_json(user, file_id, WorkflowFile.WORKFLOW_STATE)
    except WorkflowNotFoundError as exc:
        raise WorkflowStateNotFoundError(file_id) from exc
    if stored is None:
        raise WorkflowStateNotFoundError(file_id)

    state = WorkflowState.from_store(stored.data, file_id)
    if state is None:
        raise WorkflowStateNotFoundError(file_id)

    choices = ConfirmedMappingChoices.from_raw(manual_overrides, column_renames)
    updated = state.with_mapping_choices(choices)
    try:
        # Stage 2 choices are the canonical handoff to Stage 3; reject stale
        # writes so a second tab cannot replace a newer mapping decision.
        storage.write_json(
            user,
            file_id,
            WorkflowFile.WORKFLOW_STATE,
            updated.to_store(),
            expected_version=stored.version,
        )
    except WorkflowConflictError as exc:
        raise WorkflowStateConflictError(file_id) from exc
    return updated


__all__ = [
    "WorkflowStateConflictError",
    "WorkflowStateNotFoundError",
    "create_workflow_record",
    "load_workflow_state",
    "save_confirmed_mapping_choices_to_state",
    "save_initial_workflow_state",
]
