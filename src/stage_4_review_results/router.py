"""
HTTP routes for reviewing harmonized results and applying manual overrides.

Maps review HTTP requests onto Stage 4 use cases.
"""

from __future__ import annotations

from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import src.domain.dependencies as dependencies
from src.domain.schemas import DatasetWorkflowIdField
from src.domain.storage import UploadStorage
from src.stage_4_review_results.schemas import (
    DeleteOverridesResponse,
    NonConformantResponse,
    ReviewOverridesSchema,
    RowContextRequest,
    RowContextResponse,
    SaveOverridesRequest,
    SaveOverridesResponse,
    StageFourResultsRequest,
    StageFourResultsResponse,
    TermRowIndicesRequest,
    TermRowIndicesResponse,
)
from src.stage_4_review_results.use_cases import (
    RowContextUploadNotFoundError,
    StageFourRowsManifestNotFoundError,
    StageFourRowsUploadNotFoundError,
    TermRowIndicesManifestNotFoundError,
    build_non_conformant_values,
    build_row_context,
    build_stage_four_rows,
    delete_review_overrides,
    find_term_row_indices,
    get_review_overrides,
    save_review_overrides,
)

_MODULE_DIR = FilePath(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"


_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


stage_four_router = APIRouter(prefix="/stage-4", tags=["Stage 4 Review"])


@stage_four_router.get("", response_class=HTMLResponse, name="stage_four_review_page")
async def render_stage_four(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "results_endpoint": request.url_for("stage_four_harmonized_rows"),
    }
    return _templates.TemplateResponse(request, "stage_4_review.html", context)


@stage_four_router.post("/rows", response_model=StageFourResultsResponse, name="stage_four_harmonized_rows")
async def fetch_stage_four_rows(payload: StageFourResultsRequest) -> StageFourResultsResponse:
    storage: UploadStorage = dependencies.get_upload_storage()
    try:
        return build_stage_four_rows(
            file_id=payload.file_id,
            upload_storage=storage,
            workflow_storage=dependencies.get_workflow_storage(),
            user=dependencies.get_user_context(),
        )
    except StageFourRowsUploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.") from exc
    except StageFourRowsManifestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Harmonization manifest not found. Please rerun Stage 3.") from exc


DatasetWorkflowIdPath = Annotated[DatasetWorkflowIdField, Path()]


@stage_four_router.get(
    "/overrides/{file_id}",
    response_model=ReviewOverridesSchema | None,
    name="stage_four_get_overrides",
)
async def get_overrides(file_id: DatasetWorkflowIdPath) -> ReviewOverridesSchema | None:
    return get_review_overrides(
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
        file_id=file_id,
    )


@stage_four_router.post("/overrides", response_model=SaveOverridesResponse, name="stage_four_save_overrides")
async def save_overrides(payload: SaveOverridesRequest) -> SaveOverridesResponse:
    storage = dependencies.get_upload_storage()
    result = save_review_overrides(
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
        upload_storage=storage,
        file_id=payload.file_id,
        overrides=payload.overrides,
        review_state=payload.review_state,
    )
    return SaveOverridesResponse(file_id=result.file_id, updated_at=result.updated_at)


@stage_four_router.delete(
    "/overrides/{file_id}",
    response_model=DeleteOverridesResponse,
    name="stage_four_delete_overrides",
)
async def delete_overrides(file_id: DatasetWorkflowIdPath) -> DeleteOverridesResponse:
    return delete_review_overrides(
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
        file_id=file_id,
    )


@stage_four_router.get(
    "/non-conformant/{file_id}",
    response_model=NonConformantResponse,
    name="stage_four_non_conformant",
)
async def get_non_conformant_values(file_id: DatasetWorkflowIdPath) -> NonConformantResponse:
    """Deduplicate by (column, original, final) to match Stage 5's unique mapping logic."""
    storage = dependencies.get_upload_storage()
    return build_non_conformant_values(
        file_id=file_id,
        upload_storage=storage,
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
    )


@stage_four_router.post(
    "/row-context",
    response_model=RowContextResponse,
    name="stage_four_row_context",
)
async def get_row_context(payload: RowContextRequest) -> RowContextResponse:
    """On-demand fetch avoids loading full spreadsheet into review state."""
    storage = dependencies.get_upload_storage()
    try:
        return build_row_context(
            file_id=payload.file_id,
            row_indices=payload.row_indices,
            upload_storage=storage,
            workflow_storage=dependencies.get_workflow_storage(),
            user=dependencies.get_user_context(),
        )
    except RowContextUploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Upload not found") from exc


@stage_four_router.post(
    "/term-row-indices",
    response_model=TermRowIndicesResponse,
    name="stage_four_term_row_indices",
)
async def get_term_row_indices(payload: TermRowIndicesRequest) -> TermRowIndicesResponse:
    """Fetch full row indices for a term when truncated in initial response."""
    storage = dependencies.get_upload_storage()
    try:
        return find_term_row_indices(
            file_id=payload.file_id,
            column_key=payload.column_key,
            original_value=payload.original_value,
            upload_storage=storage,
            workflow_storage=dependencies.get_workflow_storage(),
            user=dependencies.get_user_context(),
        )
    except TermRowIndicesManifestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Manifest not found") from exc
