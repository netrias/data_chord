"""Capacity-based cleanup for temporary portable workflows.

Axis of change: deciding when old local workflows may be removed. Workflow
storage still owns paths, access metadata, locking, and deletion.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.domain.dataset_workflow_ids import DatasetWorkflowId

from .upload_storage import UploadStorage
from .workflow_storage import LocalWorkflowStorage, StoredWorkflowUsage

_HIGH_WATER_PERCENT = 80
_TARGET_PERCENT = 70
_RECENT_ACCESS_GRACE = timedelta(hours=24)

logger = logging.getLogger(__name__)


class WorkflowStorageFullError(Exception):
    """Raised before upload when the real filesystems have no safe write margin."""


@dataclass(frozen=True)
class WorkflowCleanupResult:
    usage_bytes_before: int
    usage_bytes_after: int
    target_bytes: int
    deleted_workflow_ids: tuple[DatasetWorkflowId, ...]


class WorkflowCleanup:
    """Enforce one portable workflow quota without delaying upload responses."""

    def __init__(
        self,
        workflow_storage: LocalWorkflowStorage,
        upload_storage: UploadStorage,
        capacity_bytes: int,
        required_free_bytes: int = 0,
    ) -> None:
        if capacity_bytes < 1:
            raise ValueError("Workflow capacity must be positive")
        if required_free_bytes < 0:
            raise ValueError("Required free space must not be negative")
        self._workflow_storage = workflow_storage
        self._upload_storage = upload_storage
        self._capacity_bytes = min(capacity_bytes, workflow_storage.filesystem_capacity_bytes())
        self._required_free_bytes = required_free_bytes

    def require_upload_space(self) -> None:
        """Try emergency cleanup, then fail before writing when disk remains full."""
        if self._required_free_bytes == 0:
            return
        if self._available_write_bytes() >= self._required_free_bytes:
            return
        self._run(emergency=True)
        if self._available_write_bytes() < self._required_free_bytes:
            raise WorkflowStorageFullError("Not enough disk space is available for another upload")

    def acquire_upload_lease(self) -> Callable[[], None]:
        try:
            return self._workflow_storage.acquire_upload_lease()
        except OSError as exc:
            raise WorkflowStorageFullError("The workflow volume cannot reserve an upload") from exc

    def _available_write_bytes(self) -> int:
        return min(
            self._workflow_storage.available_bytes(),
            self._upload_storage.available_bytes(),
        )

    def run_safely(self) -> None:
        """Background entry point that cannot disable later cleanup after a failure."""
        try:
            self.run()
        except Exception:
            logger.exception("Portable workflow cleanup failed")

    def run(self, now: datetime | None = None) -> WorkflowCleanupResult:
        return self._run(now=now, emergency=False)

    def _run(
        self,
        now: datetime | None = None,
        *,
        emergency: bool,
    ) -> WorkflowCleanupResult:
        cleanup_time = now or datetime.now(UTC)
        if cleanup_time.utcoffset() is None:
            raise ValueError("Cleanup time must include a timezone")
        target_bytes = self._threshold_bytes(_TARGET_PERCENT)
        with self._workflow_storage.try_cleanup_lease() as acquired:
            inventory = self._workflow_storage.workflow_inventory()
            below_high_water = inventory.usage_bytes <= self._threshold_bytes(_HIGH_WATER_PERCENT)
            enough_free_space = self._available_write_bytes() >= self._required_free_bytes
            if not acquired or (emergency and enough_free_space) or (not emergency and below_high_water):
                return WorkflowCleanupResult(
                    usage_bytes_before=inventory.usage_bytes,
                    usage_bytes_after=inventory.usage_bytes,
                    target_bytes=target_bytes,
                    deleted_workflow_ids=(),
                )

            usage_bytes = inventory.usage_bytes
            deleted_ids: list[DatasetWorkflowId] = []
            for candidate in self._eligible_workflows(inventory.workflows, cleanup_time):
                if emergency:
                    if self._available_write_bytes() >= self._required_free_bytes:
                        break
                elif usage_bytes <= target_bytes:
                    break
                try:
                    deleted = self._workflow_storage.delete_workflow_if_last_accessed(
                        candidate.metadata.dataset_workflow_id,
                        candidate.metadata.last_accessed_at,
                        self._upload_storage.delete_workflow_files,
                    )
                except OSError:
                    logger.exception(
                        "Could not delete workflow files",
                        extra={"file_id": candidate.metadata.dataset_workflow_id},
                    )
                    continue
                if not deleted:
                    continue
                usage_bytes -= candidate.size_bytes
                deleted_ids.append(candidate.metadata.dataset_workflow_id)

            result = WorkflowCleanupResult(
                usage_bytes_before=inventory.usage_bytes,
                usage_bytes_after=max(usage_bytes, 0),
                target_bytes=target_bytes,
                deleted_workflow_ids=tuple(deleted_ids),
            )
            logger.info(
                "Portable workflow cleanup completed",
                extra={
                    "usage_bytes_before": result.usage_bytes_before,
                    "usage_bytes_after": result.usage_bytes_after,
                    "deleted_workflows": len(result.deleted_workflow_ids),
                },
            )
            return result

    def _threshold_bytes(self, percent: int) -> int:
        return self._capacity_bytes * percent // 100

    def _eligible_workflows(
        self,
        workflows: tuple[StoredWorkflowUsage, ...],
        now: datetime,
    ) -> list[StoredWorkflowUsage]:
        cutoff = now - _RECENT_ACCESS_GRACE
        return sorted(
            (
                workflow
                for workflow in workflows
                if workflow.metadata.last_accessed_at <= cutoff
                and workflow.metadata.last_accessed_at <= now
            ),
            key=lambda workflow: workflow.metadata.last_accessed_at,
        )


__all__ = [
    "WorkflowCleanup",
    "WorkflowCleanupResult",
    "WorkflowStorageFullError",
]
