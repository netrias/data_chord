"""Durable, lease-bound Stage 3 harmonization job state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final

from src.domain.harmonization import HarmonizationManifestSummary, HarmonizeStatus
from src.storage import UserContext, VersionToken, WorkflowConflictError, WorkflowFile, WorkflowStorage

_CURRENT_SCHEMA_VERSION: Final = 2
_DEFAULT_LEASE_SECONDS: Final = 45
_SAFE_FAILED_DETAIL: Final = "Harmonization failed. Please retry."


class StageThreeJobConflictError(Exception):
    """Raised when another worker changed a Stage 3 job record first."""


class StageThreeJobUnreadableError(Exception):
    """Raised when a current stored job cannot be decoded safely."""


@dataclass(frozen=True)
class StageThreeJobState:
    """Persisted status and worker ownership for one Stage 3 run."""

    polling_job_id: str
    job_id: str
    file_id: str
    status: HarmonizeStatus
    detail: str
    next_stage_url: str
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
                raise ValueError(f"StageThreeJobState.{field_name} must be timezone-aware")

    @classmethod
    def queued(
        cls,
        *,
        polling_job_id: str,
        file_id: str,
        plan_version: str,
        worker_id: str,
        next_stage_url: str,
        now: datetime | None = None,
    ) -> StageThreeJobState:
        started_at = now or datetime.now(UTC)
        return cls(
            polling_job_id=polling_job_id,
            job_id=polling_job_id,
            file_id=file_id,
            status=HarmonizeStatus.QUEUED,
            detail="Harmonization job accepted.",
            next_stage_url=next_stage_url,
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
        """Only a successful run for the current plan can unlock later stages."""
        return self.status == HarmonizeStatus.SUCCEEDED and self.plan_version in {plan_version, "legacy"}

    def lease_expired(self, *, now: datetime | None = None) -> bool:
        return self.is_active() and self.lease_expires_at <= (now or datetime.now(UTC))

    def with_heartbeat(self, *, now: datetime | None = None) -> StageThreeJobState:
        heartbeat_at = now or datetime.now(UTC)
        return replace(
            self,
            lease_expires_at=heartbeat_at + timedelta(seconds=_DEFAULT_LEASE_SECONDS),
        )

    def interrupted(self) -> StageThreeJobState:
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
            "next_stage_url": self.next_stage_url,
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
    def from_store(cls, payload: object) -> StageThreeJobState | None:
        if not isinstance(payload, Mapping):
            return None
        schema_version = payload.get("schema_version", 1)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
            raise StageThreeJobUnreadableError("Stage 3 job has an invalid schema version")
        if schema_version > _CURRENT_SCHEMA_VERSION:
            raise StageThreeJobUnreadableError(f"Stage 3 job schema {schema_version} is not supported")

        polling_job_id = _optional_string(payload.get("polling_job_id"))
        job_id = _optional_string(payload.get("job_id"))
        file_id = _optional_string(payload.get("file_id"))
        status = _status_from_store(payload.get("status"))
        detail = _optional_string(payload.get("detail"))
        next_stage_url = _optional_string(payload.get("next_stage_url"))
        started_at = _datetime_from_store(payload.get("started_at"))
        job_id_available = payload.get("job_id_available")
        manifest_summary = _manifest_summary_from_store(payload.get("manifest_summary"))

        if job_id is None or file_id is None or status is None or detail is None:
            return None
        if next_stage_url is None or started_at is None or not isinstance(job_id_available, bool):
            return None

        plan_version = _optional_string(payload.get("plan_version"))
        worker_id = _optional_string(payload.get("worker_id"))
        lease_expires_at = _datetime_from_store(payload.get("lease_expires_at"))
        if schema_version >= _CURRENT_SCHEMA_VERSION and (
            plan_version is None or worker_id is None or lease_expires_at is None
        ):
            raise StageThreeJobUnreadableError("Stage 3 job is missing lease or plan identity")

        return cls(
            polling_job_id=polling_job_id or job_id,
            job_id=job_id,
            file_id=file_id,
            status=status,
            detail=_SAFE_FAILED_DETAIL if status == HarmonizeStatus.FAILED else detail,
            next_stage_url=next_stage_url,
            started_at=started_at,
            plan_version=plan_version or "legacy",
            worker_id=worker_id or "legacy",
            lease_expires_at=lease_expires_at or started_at,
            job_id_available=job_id_available,
            manifest_summary=manifest_summary,
        )


@dataclass(frozen=True)
class LoadedStageThreeJobState:
    job: StageThreeJobState
    version: VersionToken


def load_stage_three_job_state(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> LoadedStageThreeJobState | None:
    stored = workflow_storage.read_json(user, file_id, WorkflowFile.STAGE_THREE_JOB)
    if stored is None:
        return None
    job = StageThreeJobState.from_store(stored.data)
    if job is None:
        raise StageThreeJobUnreadableError(file_id)
    return LoadedStageThreeJobState(job=job, version=stored.version)


def save_stage_three_job_state(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    job: StageThreeJobState,
    *,
    expected_version: VersionToken | None,
) -> LoadedStageThreeJobState:
    try:
        stored = workflow_storage.write_json(
            user,
            job.file_id,
            WorkflowFile.STAGE_THREE_JOB,
            job.to_store(),
            expected_version=expected_version,
        )
    except WorkflowConflictError as exc:
        raise StageThreeJobConflictError(job.file_id) from exc
    return LoadedStageThreeJobState(job=job, version=stored.version)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _status_from_store(value: object) -> HarmonizeStatus | None:
    if not isinstance(value, str):
        return None
    try:
        return HarmonizeStatus(value)
    except ValueError:
        return None


def _datetime_from_store(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _manifest_summary_from_store(value: object) -> HarmonizationManifestSummary | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise StageThreeJobUnreadableError("Stage 3 job manifest summary must be an object")
    try:
        return HarmonizationManifestSummary.model_validate(value)
    except ValueError as exc:
        raise StageThreeJobUnreadableError("Stage 3 job manifest summary is invalid") from exc


__all__ = [
    "LoadedStageThreeJobState",
    "StageThreeJobConflictError",
    "StageThreeJobState",
    "StageThreeJobUnreadableError",
    "load_stage_three_job_state",
    "save_stage_three_job_state",
]
