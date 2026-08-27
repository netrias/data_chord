"""Durable harmonization job acceptance, leases, status, and completion."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from src.app.harmonization_results import HarmonizationWorkflowResult
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
from src.storage import UploadStorage, UserContext, VersionToken, WorkflowFile, WorkflowStorage

_HEARTBEAT_SECONDS = 15
logger = logging.getLogger(__name__)


class HarmonizationStartConflictError(Exception):
    """Raised when a different workflow plan already has an active run."""


class HarmonizationWorkflowNotFoundError(Exception):
    """Raised when Stage 3 has no authorized upload or durable workflow plan."""


class HarmonizationWorkflowUnreadableError(Exception):
    """Raised when stored workflow or run state cannot be decoded safely."""


class HarmonizationCapacityError(Exception):
    """Raised when the process cannot accept another active harmonization."""


class StaleHarmonizationWorkerError(Exception):
    """Raised when a superseded worker tries to publish or complete a run."""


@dataclass(frozen=True)
class HarmonizationStart:
    loaded_job: LoadedHarmonizationJob
    loaded_state: LoadedWorkflowState
    should_run: bool


@dataclass(frozen=True)
class HarmonizationArtifactVersions:
    """Versions captured before a worker starts replacing Stage 3 artifacts."""

    review_overrides: VersionToken | None
    cde_mapping: VersionToken | None
    pv_manifest: VersionToken | None
    harmonized_output: VersionToken | None
    manifest: VersionToken | None


def capture_harmonization_artifact_versions(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> HarmonizationArtifactVersions:
    """Capture replace/delete guards before provider work can take time."""
    review_overrides = workflow_storage.read_json(
        user,
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
    )
    cde_mapping = workflow_storage.read_json(user, file_id, WorkflowFile.CDE_MAPPING)
    pv_manifest = workflow_storage.read_json(user, file_id, WorkflowFile.PV_MANIFEST)
    return HarmonizationArtifactVersions(
        review_overrides=review_overrides.version if review_overrides is not None else None,
        cde_mapping=cde_mapping.version if cde_mapping is not None else None,
        pv_manifest=pv_manifest.version if pv_manifest is not None else None,
        harmonized_output=workflow_storage.artifact_version(
            user,
            file_id,
            WorkflowFile.HARMONIZED_OUTPUT,
        ),
        manifest=workflow_storage.artifact_version(
            user,
            file_id,
            WorkflowFile.HARMONIZATION_MANIFEST_BASE,
        ),
    )


def start_harmonization(
    *,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    polling_job_id: str | None = None,
    reserve_capacity: Callable[[], bool] | None = None,
    release_capacity: Callable[[], None] | None = None,
) -> HarmonizationStart:
    """Persist an accepted run before the endpoint returns success."""
    if load_upload_artifact(upload_storage, workflow_storage, user, file_id) is None:
        raise HarmonizationWorkflowNotFoundError(file_id)

    loaded_state = _load_current_workflow_state(
        workflow_storage=workflow_storage,
        user=user,
        file_id=file_id,
    )
    try:
        existing = load_harmonization_job(workflow_storage, user, file_id)
    except HarmonizationJobUnreadableError as exc:
        raise HarmonizationWorkflowUnreadableError(file_id) from exc

    if existing is not None and existing.job.lease_expired():
        try:
            existing = save_harmonization_job(
                workflow_storage,
                user,
                existing.job.interrupted(),
                expected_version=existing.version,
            )
        except HarmonizationJobConflictError as exc:
            raise HarmonizationStartConflictError(file_id) from exc

    if existing is not None and existing.job.is_active():
        if existing.job.plan_version == loaded_state.version.value:
            return HarmonizationStart(existing, loaded_state, should_run=False)
        raise HarmonizationStartConflictError(file_id)

    loaded_job = _accept_harmonization_job(
        workflow_storage=workflow_storage,
        user=user,
        file_id=file_id,
        plan_version=loaded_state.version.value,
        polling_job_id=polling_job_id,
        expected_version=existing.version if existing is not None else None,
        reserve_capacity=reserve_capacity,
        release_capacity=release_capacity,
    )
    return HarmonizationStart(loaded_job, loaded_state, should_run=True)


def _accept_harmonization_job(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    plan_version: str,
    polling_job_id: str | None,
    expected_version: VersionToken | None,
    reserve_capacity: Callable[[], bool] | None,
    release_capacity: Callable[[], None] | None,
) -> LoadedHarmonizationJob:
    capacity_reserved = reserve_capacity is not None
    if reserve_capacity is not None and not reserve_capacity():
        raise HarmonizationCapacityError(file_id)
    try:
        job = HarmonizationJobState.queued(
            polling_job_id=polling_job_id or uuid4().hex,
            file_id=file_id,
            plan_version=plan_version,
            worker_id=uuid4().hex,
        )
        return save_harmonization_job(
            workflow_storage,
            user,
            job,
            expected_version=expected_version,
        )
    except BaseException as exc:
        if capacity_reserved and release_capacity is not None:
            release_capacity()
        if isinstance(exc, HarmonizationJobConflictError):
            raise HarmonizationStartConflictError(file_id) from exc
        raise


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

    @property
    def worker_id(self) -> str:
        """Return the unique run identity used for private scratch files."""
        return self._accepted_job.worker_id

    def require_current(self) -> LoadedHarmonizationJob:
        loaded = load_harmonization_job(
            self._workflow_storage,
            self._user,
            self._accepted_job.file_id,
        )
        if (
            loaded is None
            or not _same_worker(loaded.job, self._accepted_job)
            or not loaded.job.is_active()
            or loaded.job.lease_expired()
        ):
            raise StaleHarmonizationWorkerError(self._accepted_job.polling_job_id)
        return loaded

    def require_plan_current(self) -> LoadedWorkflowState:
        loaded = load_workflow_state(
            self._workflow_storage,
            self._user,
            self._accepted_job.file_id,
        )
        if loaded is None or loaded.version.value != self._accepted_job.plan_version:
            raise StaleHarmonizationWorkerError(self._accepted_job.polling_job_id)
        return loaded


async def heartbeat_harmonization_job(
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
        try:
            loaded = load_harmonization_job(workflow_storage, user, accepted_job.file_id)
        except HarmonizationJobUnreadableError:
            logger.exception(
                "Harmonization heartbeat stopped because durable job state is unreadable",
                extra={"file_id": accepted_job.file_id, "job_id": accepted_job.polling_job_id},
            )
            return
        except Exception:
            logger.warning(
                "Harmonization heartbeat read failed; retrying",
                exc_info=True,
                extra={"file_id": accepted_job.file_id, "job_id": accepted_job.polling_job_id},
            )
            continue
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
        except Exception:
            logger.warning(
                "Harmonization heartbeat write failed; retrying",
                exc_info=True,
                extra={"file_id": accepted_job.file_id, "job_id": accepted_job.polling_job_id},
            )


def complete_harmonization_job(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    accepted_job: HarmonizationJobState,
    response: HarmonizationWorkflowResult,
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
        raise StaleHarmonizationWorkerError(accepted_job.polling_job_id) from exc


def fail_harmonization_job(
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
    except (HarmonizationJobConflictError, StaleHarmonizationWorkerError):
        return


def _load_current_workflow_state(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> LoadedWorkflowState:
    try:
        loaded = load_workflow_state(workflow_storage, user, file_id)
    except WorkflowStateUnreadableError as exc:
        raise HarmonizationWorkflowUnreadableError(file_id) from exc

    if loaded is None:
        raise HarmonizationWorkflowNotFoundError(file_id)

    if loaded.state.mapping_choices is None:
        raise HarmonizationStartConflictError(file_id)
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
    "HarmonizationArtifactVersions",
    "HarmonizationStart",
    "HarmonizationStartConflictError",
    "HarmonizationWorkflowNotFoundError",
    "HarmonizationCapacityError",
    "HarmonizationWorkflowUnreadableError",
    "RunAuthority",
    "StaleHarmonizationWorkerError",
    "complete_harmonization_job",
    "capture_harmonization_artifact_versions",
    "fail_harmonization_job",
    "heartbeat_harmonization_job",
    "load_authorized_job",
    "start_harmonization",
]
