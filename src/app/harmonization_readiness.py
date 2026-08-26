"""Require exact current harmonization results before later-stage work."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.dataset_workflow_ids import DatasetWorkflowId
from src.domain.harmonization import HarmonizeStatus
from src.domain.manifest import ManifestSummary
from src.domain.review_overrides import ReviewOverrides
from src.persistence.harmonization_job_store import (
    HarmonizationJobUnreadableError,
    LoadedHarmonizationJob,
    load_harmonization_job,
)
from src.persistence.review_override_store import (
    ReviewOverridesRecord,
    ReviewOverridesUnreadableError,
    load_review_overrides_record,
)
from src.persistence.workflow_state_store import (
    LoadedWorkflowState,
    WorkflowStateUnreadableError,
    load_workflow_state,
)
from src.storage import StoredJson, UserContext, VersionToken, WorkflowFile, WorkflowStorage


class HarmonizationNotReadyError(Exception):
    """Raised when Stage 4 or 5 cannot use the current Stage 3 result."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


REVIEW_STATE_RECOVERY_DETAIL = (
    "The saved review state cannot be read. Return to Stage 3 and run harmonization again."
)

_RESULT_CHANGED_DETAIL = "The harmonization result changed while this request was running. Retry the request."


@dataclass(frozen=True)
class ReadyArtifactVersions:
    """Exact durable Stage 3 artifact versions for one ready result."""

    output: VersionToken | None
    manifest: VersionToken | None
    pv_manifest: VersionToken | None
    cde_mapping: VersionToken | None


@dataclass(frozen=True)
class ReadyHarmonization:
    """A ready workflow plus the versions that make its reads consistent."""

    workflow: LoadedWorkflowState
    job: LoadedHarmonizationJob
    artifacts: ReadyArtifactVersions

    def require_unchanged(
        self,
        storage: WorkflowStorage,
        user: UserContext,
    ) -> None:
        """Fail when a rerun changed any result during a later-stage read."""
        if _ready_artifact_versions(storage, user, self.workflow.state.file_id) != self.artifacts:
            raise HarmonizationNotReadyError(_RESULT_CHANGED_DETAIL)
        final_job = load_harmonization_job(storage, user, self.workflow.state.file_id)
        final_workflow = load_workflow_state(storage, user, self.workflow.state.file_id)
        if not self._same_workflow_and_job(final_workflow, final_job):
            raise HarmonizationNotReadyError(_RESULT_CHANGED_DETAIL)

    def _same_workflow_and_job(
        self,
        workflow: LoadedWorkflowState | None,
        job: LoadedHarmonizationJob | None,
    ) -> bool:
        return (
            workflow is not None
            and job is not None
            and workflow.version == self.workflow.version
            and job.version == self.job.version
        )


def capture_ready_harmonization(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> ReadyHarmonization:
    """Capture one ready Stage 3 generation for a consistent later-stage read."""
    workflow, job = _require_ready_workflow_and_job(storage, user, file_id)
    return ReadyHarmonization(
        workflow=workflow,
        job=job,
        artifacts=_ready_artifact_versions(storage, user, file_id),
    )


def require_ready_harmonization_workflow(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> LoadedWorkflowState:
    """Return the current plan only when its exact Stage 3 job succeeded."""
    workflow, _job = _require_ready_workflow_and_job(storage, user, file_id)
    return workflow


def _require_ready_workflow_and_job(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> tuple[LoadedWorkflowState, LoadedHarmonizationJob]:
    try:
        workflow = load_workflow_state(storage, user, str(file_id))
    except WorkflowStateUnreadableError as exc:
        raise HarmonizationNotReadyError(
            "The workflow state cannot be read. Return to Stage 2 and create the mapping again."
        ) from exc
    if workflow is None:
        raise HarmonizationNotReadyError(
            "The workflow is not ready. Return to Stage 2 and create the mapping first."
        )

    try:
        job = load_harmonization_job(storage, user, file_id)
    except HarmonizationJobUnreadableError as exc:
        raise HarmonizationNotReadyError(
            "The harmonization result cannot be read. Return to Stage 3 and run harmonization again."
        ) from exc
    if job is None:
        raise HarmonizationNotReadyError(
            "Harmonization has not run. Return to Stage 3 and start harmonization."
        )
    if job.job.status is HarmonizeStatus.QUEUED:
        raise HarmonizationNotReadyError(
            "Harmonization is still running. Wait for it to finish, then retry."
        )
    if job.job.status is HarmonizeStatus.FAILED:
        raise HarmonizationNotReadyError(
            "Harmonization failed. Return to Stage 3 and retry."
        )
    if job.job.plan_version != workflow.version.value:
        raise HarmonizationNotReadyError(
            "The harmonization result is out of date. Return to Stage 3 and run harmonization again."
        )
    return workflow, job


def _ready_artifact_versions(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> ReadyArtifactVersions:
    return ReadyArtifactVersions(
        output=storage.artifact_version(user, str(file_id), WorkflowFile.HARMONIZED_OUTPUT),
        manifest=storage.artifact_version(user, str(file_id), WorkflowFile.HARMONIZATION_MANIFEST_BASE),
        pv_manifest=_stored_version(storage.read_json(user, str(file_id), WorkflowFile.PV_MANIFEST)),
        cde_mapping=_stored_version(storage.read_json(user, str(file_id), WorkflowFile.CDE_MAPPING)),
    )


def _stored_version(stored: StoredJson | None) -> VersionToken | None:
    return stored.version if stored is not None else None


def load_readable_review_overrides_record(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> ReviewOverridesRecord | None:
    """Load current review state or return one recovery error for Stage 4 and Stage 5."""
    try:
        return load_review_overrides_record(storage, user, str(file_id))
    except ReviewOverridesUnreadableError as exc:
        raise HarmonizationNotReadyError(REVIEW_STATE_RECOVERY_DETAIL) from exc


def require_review_state_matches_manifest(
    review_overrides: ReviewOverrides | None,
    manifest: ManifestSummary,
) -> None:
    """Reject review history that does not belong to the current Stage 3 result."""
    if review_overrides is None:
        return
    expected_cells = {
        (str(row_index + 1), row.column_key): (
            row.to_harmonize,
            row.top_harmonization if row.top_harmonization.strip() else row.to_harmonize,
        )
        for row in manifest.rows
        for row_index in row.row_indices
    }
    for event in review_overrides.events:
        expected = expected_cells.get((event.row_key, event.column_key))
        if (
            expected is None
            or event.original_value != expected[0]
            or event.selected_value == expected[1]
        ):
            raise HarmonizationNotReadyError(REVIEW_STATE_RECOVERY_DETAIL)


__all__ = [
    "HarmonizationNotReadyError",
    "REVIEW_STATE_RECOVERY_DETAIL",
    "ReadyHarmonization",
    "capture_ready_harmonization",
    "load_readable_review_overrides_record",
    "require_review_state_matches_manifest",
    "require_ready_harmonization_workflow",
]
