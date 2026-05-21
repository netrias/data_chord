"""Durable Stage 3 harmonization job state.

Axis of change: browser-visible Stage 3 progress should be recoverable from
workflow storage when the process-local cache is gone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from src.domain import ManifestSummarySchema
from src.domain.harmonize import HarmonizeStatus
from src.domain.storage import UserContext, WorkflowConflictError, WorkflowFile, WorkflowStorage

_FIELD_SCHEMA_VERSION = "schema_version"
_FIELD_POLLING_JOB_ID = "polling_job_id"
_FIELD_JOB_ID = "job_id"
_FIELD_FILE_ID = "file_id"
_FIELD_STATUS = "status"
_FIELD_DETAIL = "detail"
_FIELD_NEXT_STAGE_URL = "next_stage_url"
_FIELD_STARTED_AT = "started_at"
_FIELD_JOB_ID_AVAILABLE = "job_id_available"
_FIELD_MANIFEST_SUMMARY = "manifest_summary"
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StageThreeJobState:
    """Persisted status for one Stage 3 harmonization run."""

    polling_job_id: str
    job_id: str
    file_id: str
    status: HarmonizeStatus
    detail: str
    next_stage_url: str
    started_at: datetime
    job_id_available: bool = False
    manifest_summary: ManifestSummarySchema | None = None

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("StageThreeJobState.started_at must be timezone-aware")

    def elapsed_seconds(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        return max(0, int((current_time - self.started_at).total_seconds()))

    def matches_request(self, job_id: str) -> bool:
        return job_id in {self.polling_job_id, self.job_id}

    def to_store(self) -> dict[str, object]:
        payload: dict[str, object] = {
            _FIELD_SCHEMA_VERSION: _SCHEMA_VERSION,
            _FIELD_POLLING_JOB_ID: self.polling_job_id,
            _FIELD_JOB_ID: self.job_id,
            _FIELD_FILE_ID: self.file_id,
            _FIELD_STATUS: self.status.value,
            _FIELD_DETAIL: self.detail,
            _FIELD_NEXT_STAGE_URL: self.next_stage_url,
            _FIELD_STARTED_AT: self.started_at.isoformat(),
            _FIELD_JOB_ID_AVAILABLE: self.job_id_available,
        }
        if self.manifest_summary is not None:
            payload[_FIELD_MANIFEST_SUMMARY] = self.manifest_summary.model_dump(mode="json")
        return payload

    @classmethod
    def from_store(cls, payload: object) -> StageThreeJobState | None:
        if not isinstance(payload, Mapping):
            return None

        polling_job_id = _optional_string(payload.get(_FIELD_POLLING_JOB_ID))
        job_id = _optional_string(payload.get(_FIELD_JOB_ID))
        file_id = _optional_string(payload.get(_FIELD_FILE_ID))
        status = _status_from_store(payload.get(_FIELD_STATUS))
        detail = _optional_string(payload.get(_FIELD_DETAIL))
        next_stage_url = _optional_string(payload.get(_FIELD_NEXT_STAGE_URL))
        started_at = _datetime_from_store(payload.get(_FIELD_STARTED_AT))
        job_id_available = payload.get(_FIELD_JOB_ID_AVAILABLE)
        manifest_summary = _manifest_summary_from_store(payload.get(_FIELD_MANIFEST_SUMMARY))

        if job_id is None or file_id is None or status is None or detail is None:
            return None
        if next_stage_url is None or started_at is None or not isinstance(job_id_available, bool):
            return None

        return cls(
            polling_job_id=polling_job_id or job_id,
            job_id=job_id,
            file_id=file_id,
            status=status,
            detail=detail,
            next_stage_url=next_stage_url,
            started_at=started_at,
            job_id_available=job_id_available,
            manifest_summary=manifest_summary,
        )


def load_stage_three_job_state(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> StageThreeJobState | None:
    stored = workflow_storage.read_json(user, file_id, WorkflowFile.STAGE_THREE_JOB)
    if stored is None:
        return None
    return StageThreeJobState.from_store(stored.data)


def save_stage_three_job_state(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    job: StageThreeJobState,
) -> None:
    stored = workflow_storage.read_json(user, job.file_id, WorkflowFile.STAGE_THREE_JOB)
    expected_version = stored.version if stored is not None else None
    try:
        workflow_storage.write_json(
            user,
            job.file_id,
            WorkflowFile.STAGE_THREE_JOB,
            job.to_store(),
            expected_version=expected_version,
        )
    except WorkflowConflictError:
        latest = workflow_storage.read_json(user, job.file_id, WorkflowFile.STAGE_THREE_JOB)
        workflow_storage.write_json(
            user,
            job.file_id,
            WorkflowFile.STAGE_THREE_JOB,
            job.to_store(),
            expected_version=latest.version if latest is not None else None,
        )


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
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _manifest_summary_from_store(value: object) -> ManifestSummarySchema | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    return ManifestSummarySchema.model_validate(value)
