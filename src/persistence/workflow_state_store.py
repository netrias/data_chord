"""Persist the one canonical workflow plan with optimistic version checks."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.workflow_state import ConfirmedMappingChoices, WorkflowState, WorkflowStateSchemaError
from src.storage import (
    UserContext,
    VersionToken,
    WorkflowConflictError,
    WorkflowFile,
    WorkflowNotFoundError,
    WorkflowStorage,
)


class WorkflowStateNotFoundError(Exception):
    """Raised when a workflow has no stored state yet."""


class WorkflowStateConflictError(Exception):
    """Raised when workflow state changed during a read-modify-write update."""


class WorkflowStateUnreadableError(Exception):
    """Raised when stored workflow state exists but cannot be decoded safely."""


@dataclass(frozen=True)
class LoadedWorkflowState:
    """Canonical workflow state plus the storage token read with it."""

    state: WorkflowState
    version: VersionToken


def save_initial_workflow_state(
    storage: WorkflowStorage,
    user: UserContext,
    state: WorkflowState,
) -> LoadedWorkflowState:
    """Create or replace the workflow plan."""
    existing = storage.read_json(user, state.file_id, WorkflowFile.WORKFLOW_STATE)
    return save_workflow_state(
        storage,
        user,
        state,
        expected_version=existing.version if existing is not None else None,
    )


def save_workflow_state(
    storage: WorkflowStorage,
    user: UserContext,
    state: WorkflowState,
    *,
    expected_version: VersionToken | None,
) -> LoadedWorkflowState:
    """Compare-and-swap the canonical record."""
    try:
        stored = storage.write_json(
            user,
            state.file_id,
            WorkflowFile.WORKFLOW_STATE,
            state.to_store(),
            expected_version=expected_version,
        )
    except WorkflowConflictError as exc:
        raise WorkflowStateConflictError(state.file_id) from exc
    return LoadedWorkflowState(state=state, version=stored.version)


def load_workflow_state(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> LoadedWorkflowState | None:
    """Load the current canonical workflow state."""
    try:
        stored = storage.read_json(user, file_id, WorkflowFile.WORKFLOW_STATE)
    except WorkflowNotFoundError:
        return None
    if stored is None:
        return None
    try:
        state = WorkflowState.from_store(stored.data, file_id)
    except WorkflowStateSchemaError as exc:
        raise WorkflowStateUnreadableError(file_id) from exc
    return LoadedWorkflowState(state=state, version=stored.version)


def save_confirmed_mapping_choices_to_state(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    choices: ConfirmedMappingChoices,
) -> LoadedWorkflowState:
    loaded = load_workflow_state(storage, user, file_id)
    if loaded is None:
        raise WorkflowStateNotFoundError(file_id)
    return save_workflow_state(
        storage,
        user,
        loaded.state.with_mapping_choices(choices),
        expected_version=loaded.version,
    )


__all__ = [
    "LoadedWorkflowState",
    "WorkflowStateConflictError",
    "WorkflowStateNotFoundError",
    "WorkflowStateUnreadableError",
    "load_workflow_state",
    "save_confirmed_mapping_choices_to_state",
    "save_initial_workflow_state",
    "save_workflow_state",
]
