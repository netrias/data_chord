"""HTTP adapter for versioned harmonization jobs and signed artifacts."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from netrias_client import TabularFormat, read_tabular, write_tabular

import src.app.dependencies as dependencies
from src.app.harmonization_job_state import (
    HarmonizationCapacityError,
    HarmonizationStartConflictError,
    HarmonizationWorkflowNotFoundError,
    HarmonizationWorkflowUnreadableError,
)
from src.app.programmatic_harmonization import (
    ProgrammaticHarmonizationDocument,
    ProgrammaticHarmonizationRequest,
    submit_programmatic_harmonization,
)
from src.auth.user_context import signed_programmatic_artifact_query
from src.domain.columns import column_key_for_index
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import (
    DatasetWorkflowId,
    dataset_workflow_id_from_value,
)
from src.domain.harmonization import HarmonizeStatus
from src.domain.manifest import (
    ColumnMappingManifest,
    ColumnMappingRecord,
    MappingAlternative,
)
from src.persistence.harmonization_job_store import HarmonizationJobState
from src.programmatic_api.harmonization_payload import (
    HarmonizationPayloadTooLargeError,
    InvalidHarmonizationPayloadError,
    decode_harmonization_payload,
)
from src.programmatic_api.schemas import (
    HarmonizationJobResponse,
    HarmonizationSubmissionRequest,
    HarmonizationSubmitResponse,
)
from src.storage import (
    UnsupportedUploadError,
    UploadTooLargeError,
    UserContext,
    WorkflowFile,
    WorkflowStorage,
)

harmonization_router = APIRouter()


@harmonization_router.post(
    "/jobs/harmonize",
    response_model=HarmonizationSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_harmonization(request: Request) -> HarmonizationSubmitResponse:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Harmonization requests must use application/octet-stream.",
        )
    try:
        submission = await asyncio.to_thread(
            decode_harmonization_payload,
            await request.body(),
        )
        loaded = await submit_programmatic_harmonization(
            _application_request(submission),
            user=dependencies.get_user_context(),
            upload_storage=dependencies.get_upload_storage(),
            workflow_storage=dependencies.get_workflow_storage(),
            job_service=dependencies.get_harmonization_job_service(),
        )
    except HarmonizationPayloadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Harmonization request is too large.",
        ) from exc
    except InvalidHarmonizationPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Harmonization request is invalid.",
        ) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Harmonization document is too large.",
        ) from exc
    except UnsupportedUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Harmonization document is invalid.",
        ) from exc
    except HarmonizationCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Harmonization capacity is full.",
        ) from exc
    except HarmonizationWorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Harmonization workflow was not created.",
        ) from exc
    except (HarmonizationStartConflictError, HarmonizationWorkflowUnreadableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Harmonization workflow state changed.",
        ) from exc
    return HarmonizationSubmitResponse(job_id=str(loaded.job.polling_job_id))


@harmonization_router.get(
    "/jobs/{job_id}",
    response_model=HarmonizationJobResponse,
    response_model_exclude_none=True,
    response_model_by_alias=True,
)
async def get_harmonization_job(job_id: str, request: Request) -> HarmonizationJobResponse:
    workflow_id = _workflow_id_from_job_id(job_id)
    try:
        loaded = dependencies.get_harmonization_job_service().get(
            user=dependencies.get_user_context(),
            file_id=workflow_id,
            requested_job_id=job_id,
        )
    except (HarmonizationStartConflictError, HarmonizationWorkflowUnreadableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Harmonization job state changed.",
        ) from exc
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Harmonization job not found.",
        )
    return _job_response(request, loaded.job)


@harmonization_router.get(
    "/jobs/{job_id}/artifacts/{kind}",
    name="programmatic_harmonization_artifact",
)
async def download_harmonization_artifact(
    job_id: str,
    kind: str,
) -> StreamingResponse:
    if kind not in {"harmonized", "manifest"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    workflow_id = _workflow_id_from_job_id(job_id)
    user = dependencies.get_user_context()
    loaded = dependencies.get_harmonization_job_service().get(
        user=user,
        file_id=workflow_id,
        requested_job_id=job_id,
    )
    if loaded is None or loaded.job.status is not HarmonizeStatus.SUCCEEDED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    storage = dependencies.get_workflow_storage()
    artifact_kind = (
        WorkflowFile.HARMONIZED_OUTPUT
        if kind == "harmonized"
        else WorkflowFile.HARMONIZATION_MANIFEST_BASE
    )
    if storage.artifact_version(user, workflow_id, artifact_kind) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    filename = "harmonized.csv" if kind == "harmonized" else "manifest.parquet"
    media_type = "text/csv" if kind == "harmonized" else "application/octet-stream"
    return StreamingResponse(
        _artifact_chunks(storage, user, str(workflow_id), artifact_kind),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _application_request(
    submission: HarmonizationSubmissionRequest,
) -> ProgrammaticHarmonizationRequest:
    return ProgrammaticHarmonizationRequest(
        data_model_version=DataModelVersionReference(
            submission.data_model_key,
            submission.external_version_number,
        ),
        use_cache=submission.use_cache,
        document=ProgrammaticHarmonizationDocument(
            name=submission.document.name,
            sheet_name=submission.document.sheet_name,
            header=tuple(submission.document.header),
            rows=tuple(tuple(row) for row in submission.document.rows),
        ),
        mapping_manifest=_mapping_manifest(submission),
    )


def _mapping_manifest(submission: HarmonizationSubmissionRequest) -> ColumnMappingManifest:
    records = {}
    for index, mapping in enumerate(submission.column_mappings):
        if mapping is None:
            continue
        column_key = column_key_for_index(index)
        records[column_key] = ColumnMappingRecord(
            column_key=column_key,
            column_name=mapping.column_name,
            cde_key=mapping.cde_key,
            cde_id=mapping.cde_id,
            harmonization=mapping.harmonization,
            alternatives=tuple(
                MappingAlternative(
                    target=alternative.target,
                    confidence=alternative.confidence,
                    cde_id=alternative.cde_id,
                    harmonization=alternative.harmonization,
                )
                for alternative in mapping.alternatives
            ),
        )
    return ColumnMappingManifest(records)


def _workflow_id_from_job_id(job_id: str) -> DatasetWorkflowId:
    try:
        return dataset_workflow_id_from_value(job_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Harmonization job not found.",
        ) from exc


def _job_response(request: Request, job: HarmonizationJobState) -> HarmonizationJobResponse:
    if job.status is HarmonizeStatus.QUEUED:
        return HarmonizationJobResponse(status="QUEUED")
    if job.status is HarmonizeStatus.FAILED:
        return HarmonizationJobResponse(
            status="FAILED",
            errorMessage="Harmonization failed. Please retry.",
        )
    return HarmonizationJobResponse(
        status="SUCCEEDED",
        final_url=_signed_artifact_url(request, str(job.file_id), "harmonized"),
        manifest_url=_signed_artifact_url(request, str(job.file_id), "manifest"),
    )


def _signed_artifact_url(request: Request, job_id: str, kind: str) -> str:
    url = request.url_for(
        "programmatic_harmonization_artifact",
        job_id=job_id,
        kind=kind,
    )
    return str(url.include_query_params(**signed_programmatic_artifact_query(url.path)))


def _artifact_chunks(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    kind: WorkflowFile,
) -> Iterator[bytes]:
    with storage.materialize_artifact(user, file_id, kind) as materialized:
        if kind is WorkflowFile.HARMONIZED_OUTPUT:
            with TemporaryDirectory(prefix="data-chord-artifact-") as temp_dir:
                csv_path = Path(temp_dir) / "harmonized.csv"
                dataset = read_tabular(materialized)
                write_tabular(
                    csv_path,
                    replace(dataset, source_format=TabularFormat.CSV, sheet_name=None),
                )
                yield from _file_chunks(csv_path)
            return
        yield from _file_chunks(materialized)


def _file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            yield chunk


__all__ = ["harmonization_router"]
