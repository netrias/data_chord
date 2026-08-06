"""Stage 3 commands for durable acceptance, polling, leases, and completion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from src.api.schemas import HarmonizeRequest, HarmonizeResponse
from src.domain.harmonization import HarmonizeStatus
from src.persistence.harmonization_job_store import (
    HarmonizationJobConflictError,
    HarmonizationJobState,
    HarmonizationJobUnreadableError,
    LoadedHarmonizationJob,
    load_harmonization_job,
    save_harmonization_job,
)
from src.persistence.workflow_artifacts import load_upload_artifact
from src.persistence.workflow_state_store import (
    LoadedWorkflowState,
    WorkflowStateUnreadableError,
    load_workflow_state,
)
from src.storage import UploadStorage, UserContext, WorkflowStorage

_HEARTBEAT_SECONDS = 15


class HarmonizationStartConflictError(Exception):
    """Raised when a different workflow plan already has an active run."""


class HarmonizationWorkflowNotFoundError(Exception):
    """Raised when Stage 3 has no authorized upload or durable workflow plan."""


class HarmonizationWorkflowUnreadableError(Exception):
    """Raised when stored workflow or run state cannot be decoded safely."""


class StaleStageThreeWorkerError(Exception):
    """Raised when a superseded worker tries to publish or complete a run."""


@dataclass(frozen=True)
class HarmonizationStart:
    loaded_job: LoadedHarmonizationJob
    loaded_state: LoadedWorkflowState
    should_run: bool


def start_harmonization(
    *,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    payload: HarmonizeRequest,
) -> HarmonizationStart:
    """Persist an accepted run before the endpoint returns success."""
    if load_upload_artifact(upload_storage, workflow_storage, user, payload.file_id) is None:
        raise HarmonizationWorkflowNotFoundError(payload.file_id)

    loaded_state = _load_current_workflow_state(
        workflow_storage=workflow_storage,
        user=user,
        payload=payload,
    )
    try:
        existing = load_harmonization_job(workflow_storage, user, payload.file_id)
    except HarmonizationJobUnreadableError as exc:
        raise HarmonizationWorkflowUnreadableError(payload.file_id) from exc

    if existing is not None and existing.job.lease_expired():
        try:
            existing = save_harmonization_job(
                workflow_storage,
                user,
                existing.job.interrupted(),
                expected_version=existing.version,
            )
        except HarmonizationJobConflictError as exc:
            raise HarmonizationStartConflictError(payload.file_id) from exc

    if existing is not None and existing.job.is_active():
        if existing.job.plan_version == loaded_state.version.value:
            return HarmonizationStart(existing, loaded_state, should_run=False)
        raise HarmonizationStartConflictError(payload.file_id)

    polling_job_id = uuid4().hex
    job = HarmonizationJobState.queued(
        polling_job_id=polling_job_id,
        file_id=str(payload.file_id),
        plan_version=loaded_state.version.value,
        worker_id=uuid4().hex,
    )
    try:
        loaded_job = save_harmonization_job(
            workflow_storage,
            user,
            job,
            expected_version=existing.version if existing is not None else None,
        )
    except HarmonizationJobConflictError as exc:
        raise HarmonizationStartConflictError(payload.file_id) from exc
    return HarmonizationStart(loaded_job, loaded_state, should_run=True)


def load_authorized_job(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    requested_job_id: str,
) -> LoadedHarmonizationJob | None:
    """Authorize through durable storage and recover expired process-owned runs."""
    try:
        loaded = load_harmonization_job(workflow_storage, user, file_id)
    except HarmonizationJobUnreadableError as exc:
        raise HarmonizationWorkflowUnreadableError(file_id) from exc
    if loaded is None or not loaded.job.matches_request(requested_job_id):
        return None
    if not loaded.job.lease_expired():
        return loaded
    try:
        return save_harmonization_job(
            workflow_storage,
            user,
            loaded.job.interrupted(),
            expected_version=loaded.version,
        )
    except HarmonizationJobConflictError as exc:
        raise HarmonizationStartConflictError(file_id) from exc


class RunAuthority:
    """Lease guard checked before a worker publishes artifacts or terminal state."""

    def __init__(
        self,
        workflow_storage: WorkflowStorage,
        user: UserContext,
        accepted_job: HarmonizationJobState,
    ) -> None:
        self._workflow_storage = workflow_storage
        self._user = user
        self._accepted_job = accepted_job

    def require_current(self) -> LoadedHarmonizationJob:
        loaded = load_harmonization_job(
            self._workflow_storage,
            self._user,
            self._accepted_job.file_id,
        )
        if loaded is None or not _same_worker(loaded.job, self._accepted_job) or not loaded.job.is_active():
            raise StaleStageThreeWorkerError(self._accepted_job.polling_job_id)
        return loaded

    def require_plan_current(self) -> LoadedWorkflowState:
        loaded = load_workflow_state(
            self._workflow_storage,
            self._user,
            self._accepted_job.file_id,
        )
        if loaded is None or loaded.version.value != self._accepted_job.plan_version:
            raise StaleStageThreeWorkerError(self._accepted_job.polling_job_id)
        return loaded


async def heartbeat_stage_three_job(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    accepted_job: HarmonizationJobState,
    stop: asyncio.Event,
) -> None:
    """Extend the process-owned lease while the provider operation is alive."""
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=_HEARTBEAT_SECONDS)
            return
        except TimeoutError:
            pass
        loaded = load_harmonization_job(workflow_storage, user, accepted_job.file_id)
        if loaded is None or not _same_worker(loaded.job, accepted_job) or not loaded.job.is_active():
            return
        try:
            save_harmonization_job(
                workflow_storage,
                user,
                loaded.job.with_heartbeat(),
                expected_version=loaded.version,
            )
        except HarmonizationJobConflictError:
            return


def complete_stage_three_job(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    accepted_job: HarmonizationJobState,
    response: HarmonizeResponse,
) -> LoadedHarmonizationJob:
    authority = RunAuthority(workflow_storage, user, accepted_job)
    loaded = authority.require_current()
    authority.require_plan_current()
    completed = replace(
        loaded.job,
        job_id=response.job_id,
        status=response.status,
        detail=_safe_detail(response.status, response.detail),
        job_id_available=response.job_id_available,
        manifest_summary=response.manifest_summary,
        lease_expires_at=datetime.now(UTC),
    )
    try:
        return save_harmonization_job(
            workflow_storage,
            user,
            completed,
            expected_version=loaded.version,
        )
    except HarmonizationJobConflictError as exc:
        raise StaleStageThreeWorkerError(accepted_job.polling_job_id) from exc


def fail_stage_three_job(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    accepted_job: HarmonizationJobState,
) -> None:
    try:
        loaded = RunAuthority(workflow_storage, user, accepted_job).require_current()
        failed = replace(
            loaded.job,
            status=HarmonizeStatus.FAILED,
            detail="Harmonization failed. Please retry.",
            job_id_available=False,
            lease_expires_at=datetime.now(UTC),
        )
        save_harmonization_job(
            workflow_storage,
            user,
            failed,
            expected_version=loaded.version,
        )
    except (HarmonizationJobConflictError, StaleStageThreeWorkerError):
        return


def _load_current_workflow_state(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    payload: HarmonizeRequest,
) -> LoadedWorkflowState:
    try:
        loaded = load_workflow_state(workflow_storage, user, payload.file_id)
    except WorkflowStateUnreadableError as exc:
        raise HarmonizationWorkflowUnreadableError(payload.file_id) from exc

    if loaded is None:
        raise HarmonizationWorkflowNotFoundError(payload.file_id)

    if loaded.state.mapping_choices is None:
        raise HarmonizationStartConflictError(payload.file_id)
    return loaded


def _same_worker(current: HarmonizationJobState, accepted: HarmonizationJobState) -> bool:
    return (
        current.polling_job_id == accepted.polling_job_id
        and current.worker_id == accepted.worker_id
        and current.plan_version == accepted.plan_version
    )


def _safe_detail(status: HarmonizeStatus, detail: str) -> str:
    return detail if status != HarmonizeStatus.FAILED else "Harmonization failed. Please retry."


__all__ = [
    "HarmonizationStart",
    "HarmonizationStartConflictError",
    "HarmonizationWorkflowNotFoundError",
    "HarmonizationWorkflowUnreadableError",
    "RunAuthority",
    "StaleStageThreeWorkerError",
    "complete_stage_three_job",
    "fail_stage_three_job",
    "heartbeat_stage_three_job",
    "load_authorized_job",
    "start_harmonization",
]
