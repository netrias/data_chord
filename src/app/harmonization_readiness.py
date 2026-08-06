"""Require exact current harmonization results before later-stage work."""

from __future__ import annotations

from src.domain.dataset_workflow_ids import DatasetWorkflowId
from src.domain.harmonization import HarmonizeStatus
from src.persistence.harmonization_job_store import (
    HarmonizationJobUnreadableError,
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
from src.storage import UserContext, WorkflowStorage


class HarmonizationNotReadyError(Exception):
    """Raised when Stage 4 or 5 cannot use the current Stage 3 result."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


REVIEW_STATE_RECOVERY_DETAIL = (
    "The saved review state cannot be read. Return to Stage 3 and run harmonization again."
)


def require_ready_harmonization_workflow(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> LoadedWorkflowState:
    """Return the current plan only when its exact Stage 3 job succeeded."""
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
    return workflow


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


__all__ = [
    "HarmonizationNotReadyError",
    "REVIEW_STATE_RECOVERY_DETAIL",
    "load_readable_review_overrides_record",
    "require_ready_harmonization_workflow",
]
