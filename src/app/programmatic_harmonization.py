"""Accept an inline client document into the shared harmonization workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from netrias_client import TabularFormat, dataset_from_rows, write_tabular

from src.app.harmonization_jobs import (
    HarmonizationJobRequest,
    HarmonizationJobService,
)
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import DatasetWorkflowId, new_dataset_workflow_id
from src.domain.manifest import ColumnMappingManifest
from src.domain.workflow_state import ConfirmedMappingChoices, WorkflowState
from src.persistence.harmonization_job_store import LoadedHarmonizationJob, load_harmonization_job
from src.persistence.workflow_artifacts import save_upload_artifacts, stage_upload
from src.persistence.workflow_state_store import save_initial_workflow_state
from src.storage import UploadStorage, UserContext, WorkflowStorage
from src.storage.file_operation import run_file_operation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgrammaticHarmonizationDocument:
    name: str
    sheet_name: str | None
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ProgrammaticHarmonizationRequest:
    data_model_version: DataModelVersionReference
    use_cache: bool
    document: ProgrammaticHarmonizationDocument
    mapping_manifest: ColumnMappingManifest


class _PathUpload:
    """Expose one generated tabular file through the upload storage contract."""

    def __init__(self, handle: BinaryIO, filename: str, content_type: str) -> None:
        self.filename: str | None = filename
        self.content_type: str | None = content_type
        self._handle = handle

    async def read(self, size: int = -1) -> bytes:
        return self._handle.read(size)

    async def close(self) -> None:
        self._handle.close()


async def submit_programmatic_harmonization(
    submission: ProgrammaticHarmonizationRequest,
    *,
    user: UserContext,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    job_service: HarmonizationJobService,
) -> LoadedHarmonizationJob:
    """Persist one canonical workflow, then submit it to the shared job service."""
    file_id = new_dataset_workflow_id()
    source_format = TabularFormat(Path(submission.document.name).suffix.lower().lstrip("."))
    dataset = dataset_from_rows(
        headers=list(submission.document.header),
        rows=[list(row) for row in submission.document.rows],
        source_format=source_format,
        sheet_name=submission.document.sheet_name,
    )
    workflow_created = False
    try:
        with TemporaryDirectory(prefix="data-chord-programmatic-") as temp_dir:
            source_path = Path(temp_dir) / submission.document.name
            await run_file_operation(write_tabular, source_path, dataset)
            with source_path.open("rb") as handle:
                upload = _PathUpload(handle, submission.document.name, source_format.content_type)
                async with stage_upload(upload_storage, workflow_storage, upload, file_id) as meta:
                    workflow_storage.create_workflow(user, file_id)
                    workflow_created = True
                    save_upload_artifacts(workflow_storage, user, upload_storage, meta)
        state = WorkflowState.from_data_model_version(
            file_id,
            submission.data_model_version,
            submission.mapping_manifest,
            selected_sheet=submission.document.sheet_name,
        ).with_mapping_choices(ConfirmedMappingChoices.from_raw({}, {}))
        save_initial_workflow_state(workflow_storage, user, state)
    except BaseException:
        _discard_rejected_workflow(
            workflow_created=workflow_created,
            workflow_storage=workflow_storage,
            upload_storage=upload_storage,
            user=user,
            file_id=file_id,
        )
        raise

    try:
        return await job_service.submit(
            user=user,
            request=HarmonizationJobRequest(
                file_id=file_id,
                polling_job_id=str(file_id),
                use_cache=submission.use_cache,
            ),
            # Return at the durable acceptance boundary. A startup grace wait
            # would let request cancellation delete a job whose worker started.
            start_grace_seconds=0,
        )
    except BaseException:
        # A failed response can follow a committed job write. Delete only after a confirmed miss.
        if _job_was_not_accepted(workflow_storage, user, file_id):
            _discard_rejected_workflow(
                workflow_created=True, workflow_storage=workflow_storage, upload_storage=upload_storage,
                user=user, file_id=file_id,
            )
        raise


def _job_was_not_accepted(storage: WorkflowStorage, user: UserContext, file_id: DatasetWorkflowId) -> bool:
    try:
        return load_harmonization_job(storage, user, file_id) is None
    except Exception:
        logger.exception("Could not confirm job acceptance; preserving workflow", extra={"file_id": file_id})
        return False


def _discard_rejected_workflow(
    *,
    workflow_created: bool,
    workflow_storage: WorkflowStorage,
    upload_storage: UploadStorage,
    user: UserContext,
    file_id: DatasetWorkflowId,
) -> None:
    """Remove durable and scratch data when a job was not accepted."""
    if workflow_created:
        try:
            workflow_storage.delete_workflow(user, file_id)
        except Exception:
            logger.exception(
                "Could not delete rejected programmatic workflow",
                extra={"file_id": str(file_id)},
            )
    try:
        upload_storage.delete_workflow_files(file_id)
    except Exception:
        logger.exception(
            "Could not delete rejected programmatic upload",
            extra={"file_id": str(file_id)},
        )


__all__ = [
    "ProgrammaticHarmonizationDocument",
    "ProgrammaticHarmonizationRequest",
    "submit_programmatic_harmonization",
]
