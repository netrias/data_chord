"""Persist the canonical workflow plan with optimistic version checks.

The current workflow record owns the selected model, discovered mapping, and
confirmed Stage 2 choices. A separate mapping artifact remains a temporary
compatibility projection for binaries that still read the older split shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.dataset_workflow_ids import DatasetWorkflowId
from src.domain.manifest import ColumnMappingManifest
from src.domain.workflow_state import ConfirmedMappingChoices, WorkflowState, WorkflowStateSchemaError
from src.persistence.workflow_artifacts import load_mapping_manifest
from src.storage import (
    UploadStorage,
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


def create_workflow_record(
    storage: WorkflowStorage,
    user: UserContext,
    dataset_workflow_id: DatasetWorkflowId,
) -> None:
    """Create owner metadata for a newly uploaded workflow."""
    storage.create_workflow(user, dataset_workflow_id)


def save_initial_workflow_state(
    storage: WorkflowStorage,
    user: UserContext,
    state: WorkflowState,
) -> LoadedWorkflowState:
    """Create or replace the workflow plan and its compatibility projection."""
    _require_mapping_manifest(state)
    try:
        existing = storage.read_json(user, state.file_id, WorkflowFile.WORKFLOW_STATE)
    except WorkflowNotFoundError:
        # Ownerless records are supported only while importing old local flows.
        storage.create_workflow(user, state.file_id)
        existing = None
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
    """Compare-and-swap the canonical record, then maintain the old projection."""
    manifest = _require_mapping_manifest(state)
    try:
        stored = storage.write_json(
            user,
            state.file_id,
            WorkflowFile.WORKFLOW_STATE,
            state.to_store(),
            expected_version=expected_version,
        )
        _save_mapping_projection(storage, user, state.file_id, manifest)
    except WorkflowConflictError as exc:
        raise WorkflowStateConflictError(state.file_id) from exc
    return LoadedWorkflowState(state=state, version=stored.version)


def load_workflow_state(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    *,
    legacy_upload_storage: UploadStorage | None = None,
) -> LoadedWorkflowState | None:
    """Load current state, adapting an old split mapping record in memory."""
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
    if state is None:
        raise WorkflowStateUnreadableError(file_id)

    if state.mapping_manifest is None:
        manifest_payload = (
            load_mapping_manifest(legacy_upload_storage, storage, user, file_id)
            if legacy_upload_storage is not None
            else _load_durable_mapping_projection(storage, user, file_id)
        )
        if manifest_payload is not None:
            state = state.with_mapping_manifest(ColumnMappingManifest.from_payload(manifest_payload))
    return LoadedWorkflowState(state=state, version=stored.version)


def save_confirmed_mapping_choices_to_state(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    choices: ConfirmedMappingChoices,
    *,
    legacy_upload_storage: UploadStorage | None = None,
) -> LoadedWorkflowState:
    loaded = load_workflow_state(
        storage,
        user,
        file_id,
        legacy_upload_storage=legacy_upload_storage,
    )
    if loaded is None:
        raise WorkflowStateNotFoundError(file_id)
    if loaded.state.mapping_manifest is None:
        raise WorkflowStateUnreadableError(file_id)
    return save_workflow_state(
        storage,
        user,
        loaded.state.with_mapping_choices(choices),
        expected_version=loaded.version,
    )


def _require_mapping_manifest(state: WorkflowState) -> ColumnMappingManifest:
    if state.mapping_manifest is None:
        raise WorkflowStateUnreadableError(f"Workflow state has no mapping manifest: {state.file_id}")
    return state.mapping_manifest


def _load_durable_mapping_projection(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> object | None:
    stored = storage.read_json(user, file_id, WorkflowFile.MAPPING_MANIFEST)
    return stored.data if stored is not None else None


def _save_mapping_projection(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    manifest: ColumnMappingManifest,
) -> None:
    existing = storage.read_json(user, file_id, WorkflowFile.MAPPING_MANIFEST)
    storage.write_json(
        user,
        file_id,
        WorkflowFile.MAPPING_MANIFEST,
        manifest.to_payload(),
        expected_version=existing.version if existing is not None else None,
    )


__all__ = [
    "LoadedWorkflowState",
    "WorkflowStateConflictError",
    "WorkflowStateNotFoundError",
    "WorkflowStateUnreadableError",
    "create_workflow_record",
    "load_workflow_state",
    "save_confirmed_mapping_choices_to_state",
    "save_initial_workflow_state",
    "save_workflow_state",
]
