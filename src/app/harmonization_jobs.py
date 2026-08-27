"""Shared application service for accepted harmonization jobs.

HTTP routes translate their own request and response models. This module owns
the process task, durable job state, lease heartbeat, and admission limit.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from src.app.harmonization_job_state import (
    HarmonizationStart,
    RunAuthority,
    StaleHarmonizationWorkerError,
    complete_harmonization_job,
    fail_harmonization_job,
    heartbeat_harmonization_job,
    load_authorized_job,
    start_harmonization,
)
from src.app.harmonization_results import HarmonizationWorkflowResult
from src.domain.dataset_workflow_ids import DatasetWorkflowId
from src.persistence.harmonization_job_store import LoadedHarmonizationJob
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.storage import UploadStorage, UserContext, WorkflowStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HarmonizationJobRequest:
    """Application input shared by browser and programmatic callers."""

    file_id: DatasetWorkflowId
    polling_job_id: str | None = None
    use_cache: bool = True


class HarmonizationWorkflowRunner(Protocol):
    """Execute the workflow after the job has been durably accepted."""

    async def __call__(
        self,
        *,
        file_id: DatasetWorkflowId,
        loaded_state: LoadedWorkflowState,
        authority: RunAuthority,
        user: UserContext,
        use_cache: bool,
    ) -> HarmonizationWorkflowResult: ...


class HarmonizationJobService:
    """Own local job tasks while durable storage remains the source of truth."""

    def __init__(
        self,
        *,
        upload_storage: UploadStorage,
        workflow_storage: WorkflowStorage,
        workflow_runner: HarmonizationWorkflowRunner | None = None,
        max_active_jobs: int = 1,
    ) -> None:
        if max_active_jobs < 1:
            raise ValueError("max_active_jobs must be positive")
        self._upload_storage = upload_storage
        self._workflow_storage = workflow_storage
        self._workflow_runner = workflow_runner
        self._max_active_jobs = max_active_jobs
        self._reserved_slots = 0
        self._submission_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def submit(
        self,
        *,
        user: UserContext,
        request: HarmonizationJobRequest,
        runner: HarmonizationWorkflowRunner | None = None,
        start_grace_seconds: float = 0.25,
    ) -> LoadedHarmonizationJob:
        """Accept one job and start its process-owned worker if needed."""
        selected_runner = runner or self._workflow_runner
        if selected_runner is None:
            raise ValueError("workflow_runner is required")
        async with self._submission_lock:
            start = start_harmonization(
                upload_storage=self._upload_storage,
                workflow_storage=self._workflow_storage,
                user=user,
                file_id=request.file_id,
                polling_job_id=request.polling_job_id,
                reserve_capacity=self._reserve_slot,
                release_capacity=self._release_slot,
            )
            if not start.should_run:
                return start.loaded_job

            accepted_job = start.loaded_job.job
            try:
                task = asyncio.create_task(
                    self._run(
                        start=start,
                        request=request,
                        runner=selected_runner,
                        user=user,
                    ),
                    name=f"harmonization-{accepted_job.polling_job_id}",
                )
            except BaseException:
                self._release_slot()
                raise
            self._tasks[accepted_job.polling_job_id] = task
            task.add_done_callback(
                lambda completed, job_id=accepted_job.polling_job_id: self._task_finished(job_id, completed)
            )

        if start_grace_seconds > 0:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=start_grace_seconds)
            except TimeoutError:
                pass
        return self.get(
            user=user,
            file_id=request.file_id,
            requested_job_id=accepted_job.polling_job_id,
        ) or start.loaded_job

    def get(
        self,
        *,
        user: UserContext,
        file_id: DatasetWorkflowId,
        requested_job_id: str,
    ) -> LoadedHarmonizationJob | None:
        """Load status through the existing durable authorization checks."""
        return load_authorized_job(
            workflow_storage=self._workflow_storage,
            user=user,
            file_id=file_id,
            requested_job_id=requested_job_id,
        )

    async def shutdown(self) -> None:
        """Stop local workers; durable lease recovery handles process loss."""
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _reserve_slot(self) -> bool:
        if self._reserved_slots >= self._max_active_jobs:
            return False
        self._reserved_slots += 1
        return True

    def _release_slot(self) -> None:
        self._reserved_slots = max(0, self._reserved_slots - 1)

    async def _run(
        self,
        *,
        start: HarmonizationStart,
        request: HarmonizationJobRequest,
        runner: HarmonizationWorkflowRunner,
        user: UserContext,
    ) -> None:
        accepted_job = start.loaded_job.job
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            heartbeat_harmonization_job(
                workflow_storage=self._workflow_storage,
                user=user,
                accepted_job=accepted_job,
                stop=stop_heartbeat,
            ),
            name=f"harmonization-heartbeat-{accepted_job.polling_job_id}",
        )
        try:
            response = await runner(
                file_id=request.file_id,
                loaded_state=start.loaded_state,
                authority=RunAuthority(self._workflow_storage, user, accepted_job),
                user=user,
                use_cache=request.use_cache,
            )
            complete_harmonization_job(
                workflow_storage=self._workflow_storage,
                user=user,
                accepted_job=accepted_job,
                response=response,
            )
        except StaleHarmonizationWorkerError:
            logger.warning(
                "Superseded harmonization worker stopped before publishing",
                extra={"file_id": request.file_id, "job_id": accepted_job.polling_job_id},
            )
            fail_harmonization_job(
                workflow_storage=self._workflow_storage,
                user=user,
                accepted_job=accepted_job,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive job boundary
            logger.exception(
                "Background harmonization failed",
                extra={"file_id": request.file_id, "job_id": accepted_job.polling_job_id},
            )
            fail_harmonization_job(
                workflow_storage=self._workflow_storage,
                user=user,
                accepted_job=accepted_job,
            )
        finally:
            stop_heartbeat.set()
            try:
                self._cleanup_worker_output(request.file_id, accepted_job.worker_id)
            except Exception:  # pragma: no cover - defensive cleanup boundary
                logger.exception(
                    "Could not remove harmonization scratch output",
                    extra={"file_id": request.file_id, "job_id": accepted_job.polling_job_id},
                )
            try:
                await heartbeat
            except Exception:  # pragma: no cover - defensive storage boundary
                logger.exception(
                    "Harmonization heartbeat failed during worker shutdown",
                    extra={"file_id": request.file_id, "job_id": accepted_job.polling_job_id},
                )
            finally:
                self._release_slot()

    def _task_finished(self, job_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
        if not task.cancelled():
            task.exception()

    def _cleanup_worker_output(self, file_id: DatasetWorkflowId, worker_id: str) -> None:
        meta = self._upload_storage.load(file_id)
        if meta is None:
            return
        managed_path = self._upload_storage.harmonized_path_for(file_id, meta.saved_path)
        worker_path = managed_path.with_stem(f"{managed_path.stem}.{worker_id}")
        worker_path.unlink(missing_ok=True)


__all__ = [
    "HarmonizationJobRequest",
    "HarmonizationJobService",
    "HarmonizationWorkflowRunner",
]
