"""HTTP adapter for the Stage 3 harmonization application workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import src.app.dependencies as dependencies
from src.api.schemas import DatasetWorkflowIdField, HarmonizeRequest, HarmonizeResponse
from src.app.harmonization_job_state import (
    HarmonizationCapacityError,
    HarmonizationStartConflictError,
    HarmonizationWorkflowNotFoundError,
    HarmonizationWorkflowUnreadableError,
)
from src.app.harmonization_jobs import HarmonizationJobRequest
from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_value
from src.domain.harmonization import HarmonizationFailure, HarmonizeStatus
from src.domain.reference_data import ReferenceDataError, ReferenceModelNotFoundError
from src.persistence.harmonization_job_store import HarmonizationJobState
from src.persistence.workflow_state_store import LoadedWorkflowState, WorkflowStateUnreadableError, load_workflow_state
from src.shared.jinja import templates_for_stage
from src.shared.model_recovery import REFERENCE_UNAVAILABLE_DETAIL, RETIRED_MODEL_DETAIL, model_recovery_path

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
NEXT_STAGE_PATH = "/stage-4"

_templates = templates_for_stage(TEMPLATE_DIR)
stage_three_router = APIRouter(prefix="/stage-3", tags=["Stage 3 Harmonize"])


@stage_three_router.get("", response_class=HTMLResponse, name="stage_three_entry")
async def render_stage_three(
    request: Request,
    file_id: Annotated[DatasetWorkflowIdField | None, Query()] = None,
) -> Response:
    if file_id is not None:
        try:
            await run_in_threadpool(_load_workflow_reference, file_id)
        except ReferenceModelNotFoundError:
            return RedirectResponse(model_recovery_path(request, file_id), status_code=status.HTTP_303_SEE_OTHER)
        except ReferenceDataError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, REFERENCE_UNAVAILABLE_DETAIL) from exc
        except WorkflowStateUnreadableError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Workflow state is unreadable. Please rerun analysis."
            ) from exc
    return _templates.TemplateResponse(
        request,
        "stage_3_harmonize.html",
        {"request": request, "next_stage_url": NEXT_STAGE_PATH},
    )


def _load_workflow_reference(file_id: DatasetWorkflowId) -> None:
    loaded = _load_user_workflow(file_id)
    if loaded is not None:
        dependencies.get_reference_data_repository().load_model(loaded.state.data_model_version)


def _load_user_workflow(file_id: DatasetWorkflowId) -> LoadedWorkflowState | None:
    return load_workflow_state(dependencies.get_workflow_storage(), dependencies.get_user_context(), file_id)


@stage_three_router.post(
    "/harmonize",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize",
)
async def harmonize_dataset(request: Request, payload: HarmonizeRequest) -> HarmonizeResponse:
    try:
        loaded = await dependencies.get_harmonization_job_service().submit(
            user=dependencies.get_user_context(),
            request=HarmonizationJobRequest(file_id=payload.file_id, plan_version=payload.plan_version),
        )
    except HarmonizationWorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found. Please rerun analysis.",
        ) from exc
    except HarmonizationCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Harmonization capacity is full. Please retry.",
        ) from exc
    except (HarmonizationStartConflictError, HarmonizationWorkflowUnreadableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow state changed or is unreadable. Please refresh and try again.",
        ) from exc
    return await _response_from_job(request, loaded.job)


@stage_three_router.get(
    "/jobs/{job_id}",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize_job",
)
async def get_harmonize_job(
    request: Request,
    job_id: str,
    file_id: str | None = Query(default=None),
) -> HarmonizeResponse:
    if file_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Harmonization job not found.")
    try:
        workflow_id = dataset_workflow_id_from_value(file_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Harmonization job not found.") from exc
    try:
        loaded = dependencies.get_harmonization_job_service().get(
            user=dependencies.get_user_context(),
            file_id=workflow_id,
            requested_job_id=job_id,
        )
    except (HarmonizationStartConflictError, HarmonizationWorkflowUnreadableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Harmonization state changed or is unreadable. Please refresh.",
        ) from exc
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Harmonization job not found.")
    return await _response_from_job(request, loaded.job)


async def _response_from_job(request: Request, job: HarmonizationJobState) -> HarmonizeResponse:
    detail = job.detail
    recovery_url = None
    if job.failure == HarmonizationFailure.MODEL_VERSION_RETIRED:
        try:
            loaded = await run_in_threadpool(_load_user_workflow, job.file_id)
        except WorkflowStateUnreadableError:
            loaded = None
        if loaded is not None and loaded.version.value == job.plan_version:
            detail = RETIRED_MODEL_DETAIL
            recovery_url = model_recovery_path(request, job.file_id)
        else:
            detail = "This job belongs to an earlier mapping plan. Return to Mapping to continue."
    elif job.failure == HarmonizationFailure.REFERENCE_UNAVAILABLE:
        detail = REFERENCE_UNAVAILABLE_DETAIL
    return HarmonizeResponse(
        plan_version=job.plan_version,
        job_id=job.job_id,
        status=job.status,
        detail=detail,
        recovery_url=recovery_url,
        next_stage_url=_next_stage_url(file_id=str(job.file_id), job_id=job.polling_job_id, job_status=job.status),
        job_id_available=job.job_id_available,
        elapsed_seconds=job.elapsed_seconds(),
        manifest_summary=job.manifest_summary,
    )


def _next_stage_url(*, file_id: str, job_id: str, job_status: HarmonizeStatus) -> str:
    query_params = urlencode({"file_id": file_id, "job_id": job_id, "status": job_status.value, "detail": ""})
    return f"{NEXT_STAGE_PATH}?{query_params}"


__all__ = ["stage_three_router"]
