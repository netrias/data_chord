"""Persist the current lease-bound harmonization job record."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_value
from src.domain.harmonization import HarmonizationManifestSummary, HarmonizeStatus
from src.storage import UserContext, VersionToken, WorkflowConflictError, WorkflowFile, WorkflowStorage

_CURRENT_SCHEMA_VERSION: Final = 3
_DEFAULT_LEASE_SECONDS: Final = 45
_SAFE_FAILED_DETAIL: Final = "Harmonization failed. Please retry."


class HarmonizationJobConflictError(Exception):
    """Raised when another worker changed a harmonization job first."""


class HarmonizationJobUnreadableError(Exception):
    """Raised when a stored harmonization job is not the current schema."""


@dataclass(frozen=True)
class HarmonizationJobState:
    """Status and worker ownership for one Stage 3 harmonization run."""

    polling_job_id: str
    job_id: str
    file_id: DatasetWorkflowId
    status: HarmonizeStatus
    detail: str
    started_at: datetime
    plan_version: str
    worker_id: str
    lease_expires_at: datetime
    job_id_available: bool = False
    manifest_summary: HarmonizationManifestSummary | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("started_at", self.started_at),
            ("lease_expires_at", self.lease_expires_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"HarmonizationJobState.{field_name} must be timezone-aware")

    @classmethod
    def queued(
        cls,
        *,
        polling_job_id: str,
        file_id: DatasetWorkflowId | str,
        plan_version: str,
        worker_id: str,
        now: datetime | None = None,
    ) -> HarmonizationJobState:
        started_at = now or datetime.now(UTC)
        return cls(
            polling_job_id=polling_job_id,
            job_id=polling_job_id,
            file_id=dataset_workflow_id_from_value(file_id),
            status=HarmonizeStatus.QUEUED,
            detail="Harmonization job accepted.",
            started_at=started_at,
            plan_version=plan_version,
            worker_id=worker_id,
            lease_expires_at=started_at + timedelta(seconds=_DEFAULT_LEASE_SECONDS),
        )

    def elapsed_seconds(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        return max(0, int((current_time - self.started_at).total_seconds()))

    def matches_request(self, job_id: str) -> bool:
        return job_id in {self.polling_job_id, self.job_id}

    def is_active(self) -> bool:
        return self.status == HarmonizeStatus.QUEUED

    def is_completed_for_plan(self, plan_version: str) -> bool:
        return self.status == HarmonizeStatus.SUCCEEDED and self.plan_version == plan_version

    def lease_expired(self, *, now: datetime | None = None) -> bool:
        return self.is_active() and self.lease_expires_at <= (now or datetime.now(UTC))

    def with_heartbeat(self, *, now: datetime | None = None) -> HarmonizationJobState:
        heartbeat_at = now or datetime.now(UTC)
        return replace(
            self,
            lease_expires_at=heartbeat_at + timedelta(seconds=_DEFAULT_LEASE_SECONDS),
        )

    def interrupted(self) -> HarmonizationJobState:
        return replace(
            self,
            status=HarmonizeStatus.FAILED,
            detail="Harmonization was interrupted. Please retry.",
            job_id_available=False,
        )

    def to_store(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": _CURRENT_SCHEMA_VERSION,
            "polling_job_id": self.polling_job_id,
            "job_id": self.job_id,
            "file_id": self.file_id,
            "status": self.status.value,
            "detail": self.detail,
            "started_at": self.started_at.isoformat(),
            "plan_version": self.plan_version,
            "worker_id": self.worker_id,
            "lease_expires_at": self.lease_expires_at.isoformat(),
            "job_id_available": self.job_id_available,
        }
        if self.manifest_summary is not None:
            payload["manifest_summary"] = self.manifest_summary.model_dump(mode="json")
        return payload

    @classmethod
    def from_store(
        cls,
        payload: object,
        file_id: DatasetWorkflowId | str,
    ) -> HarmonizationJobState:
        if not isinstance(payload, Mapping):
            raise HarmonizationJobUnreadableError("harmonization job must be an object")

        schema_version = payload.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise HarmonizationJobUnreadableError("harmonization job has an invalid schema version")
        if schema_version != _CURRENT_SCHEMA_VERSION:
            raise HarmonizationJobUnreadableError(
                f"harmonization job schema {schema_version} is not supported"
            )

        expected_file_id = dataset_workflow_id_from_value(file_id)
        stored_file_id = payload.get("file_id")
        if stored_file_id != expected_file_id:
            raise HarmonizationJobUnreadableError("harmonization job file identity does not match")

        polling_job_id = _required_non_empty_string(payload, "polling_job_id")
        job_id = _required_non_empty_string(payload, "job_id")
        status = _required_status(payload)
        detail = _required_string(payload, "detail")
        started_at = _required_datetime(payload, "started_at")
        plan_version = _required_non_empty_string(payload, "plan_version")
        worker_id = _required_non_empty_string(payload, "worker_id")
        lease_expires_at = _required_datetime(payload, "lease_expires_at")
        job_id_available = payload.get("job_id_available")
        if not isinstance(job_id_available, bool):
            raise HarmonizationJobUnreadableError("harmonization job has invalid job_id_available")

        return cls(
            polling_job_id=polling_job_id,
            job_id=job_id,
            file_id=expected_file_id,
            status=status,
            detail=_SAFE_FAILED_DETAIL if status == HarmonizeStatus.FAILED else detail,
            started_at=started_at,
            plan_version=plan_version,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
            job_id_available=job_id_available,
            manifest_summary=_manifest_summary_from_store(payload.get("manifest_summary")),
        )


@dataclass(frozen=True)
class LoadedHarmonizationJob:
    job: HarmonizationJobState
    version: VersionToken


def load_harmonization_job(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> LoadedHarmonizationJob | None:
    stored = storage.read_json(user, file_id, WorkflowFile.STAGE_THREE_JOB)
    if stored is None:
        return None
    job = HarmonizationJobState.from_store(stored.data, file_id)
    return LoadedHarmonizationJob(job=job, version=stored.version)


def save_harmonization_job(
    storage: WorkflowStorage,
    user: UserContext,
    job: HarmonizationJobState,
    *,
    expected_version: VersionToken | None,
) -> LoadedHarmonizationJob:
    try:
        stored = storage.write_json(
            user,
            job.file_id,
            WorkflowFile.STAGE_THREE_JOB,
            job.to_store(),
            expected_version=expected_version,
        )
    except WorkflowConflictError as exc:
        raise HarmonizationJobConflictError(job.file_id) from exc
    return LoadedHarmonizationJob(job=job, version=stored.version)


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise HarmonizationJobUnreadableError(f"harmonization job has invalid {field}")
    return value


def _required_non_empty_string(payload: Mapping[str, object], field: str) -> str:
    value = _required_string(payload, field)
    if not value:
        raise HarmonizationJobUnreadableError(f"harmonization job has invalid {field}")
    return value


def _required_status(payload: Mapping[str, object]) -> HarmonizeStatus:
    value = payload.get("status")
    if not isinstance(value, str):
        raise HarmonizationJobUnreadableError("harmonization job has invalid status")
    try:
        return HarmonizeStatus(value)
    except ValueError as exc:
        raise HarmonizationJobUnreadableError("harmonization job has invalid status") from exc


def _required_datetime(payload: Mapping[str, object], field: str) -> datetime:
    value = payload.get(field)
    if not isinstance(value, str):
        raise HarmonizationJobUnreadableError(f"harmonization job has invalid {field}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HarmonizationJobUnreadableError(f"harmonization job has invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HarmonizationJobUnreadableError(f"harmonization job has invalid {field}")
    return parsed


def _manifest_summary_from_store(value: object) -> HarmonizationManifestSummary | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise HarmonizationJobUnreadableError("harmonization job manifest summary must be an object")
    try:
        return HarmonizationManifestSummary.model_validate(value)
    except ValueError as exc:
        raise HarmonizationJobUnreadableError("harmonization job manifest summary is invalid") from exc


__all__ = [
    "HarmonizationJobConflictError",
    "HarmonizationJobState",
    "HarmonizationJobUnreadableError",
    "LoadedHarmonizationJob",
    "load_harmonization_job",
    "save_harmonization_job",
]
