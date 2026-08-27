"""HTTP adapter for the Stage 3 harmonization application workflow."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

import src.app.dependencies as dependencies
from src.api.schemas import HarmonizeRequest, HarmonizeResponse
from src.app.harmonization_job_state import (
    HarmonizationCapacityError,
    HarmonizationStartConflictError,
    HarmonizationWorkflowNotFoundError,
    HarmonizationWorkflowUnreadableError,
)
from src.app.harmonization_jobs import HarmonizationJobRequest
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_value
from src.domain.harmonization import HarmonizeStatus
from src.persistence.harmonization_job_store import HarmonizationJobState
from src.shared.jinja import templates_for_stage

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
NEXT_STAGE_PATH = "/stage-4"

_templates = templates_for_stage(TEMPLATE_DIR)
stage_three_router = APIRouter(prefix="/stage-3", tags=["Stage 3 Harmonize"])


@stage_three_router.get("", response_class=HTMLResponse, name="stage_three_entry")
async def render_stage_three(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
        "stage_3_harmonize.html",
        {"request": request, "next_stage_url": NEXT_STAGE_PATH},
    )


@stage_three_router.post(
    "/harmonize",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize",
)
async def harmonize_dataset(payload: HarmonizeRequest) -> HarmonizeResponse:
    try:
        loaded = await dependencies.get_harmonization_job_service().submit(
            user=dependencies.get_user_context(),
            request=HarmonizationJobRequest(file_id=payload.file_id),
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
    return _response_from_job(loaded.job)


@stage_three_router.get(
    "/jobs/{job_id}",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize_job",
)
async def get_harmonize_job(job_id: str, file_id: str | None = Query(default=None)) -> HarmonizeResponse:
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
    return _response_from_job(loaded.job)


def _response_from_job(job: HarmonizationJobState) -> HarmonizeResponse:
    return HarmonizeResponse(
        job_id=job.job_id,
        status=job.status,
        detail=job.detail,
        next_stage_url=_next_stage_url(file_id=str(job.file_id), job_id=job.job_id, job_status=job.status),
        job_id_available=job.job_id_available,
        elapsed_seconds=job.elapsed_seconds(),
        manifest_summary=job.manifest_summary,
    )


def _next_stage_url(*, file_id: str, job_id: str, job_status: HarmonizeStatus) -> str:
    query_params = urlencode({"file_id": file_id, "job_id": job_id, "status": job_status.value, "detail": ""})
    return f"{NEXT_STAGE_PATH}?{query_params}"


__all__ = ["stage_three_router"]
