"""
HTTP routes for reviewing harmonized results and applying manual overrides.

Maps review HTTP requests onto Stage 4 use cases.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Request, Response, status
from fastapi.responses import HTMLResponse

import src.app.dependencies as dependencies
from src.api.schemas import DatasetWorkflowIdField
from src.shared.jinja import templates_for_stage
from src.stage_4_review_results.schemas import (
    NonConformantResponse,
    ReviewOverridesSchema,
    RowContextRequest,
    RowContextResponse,
    SaveOverridesRequest,
    SaveOverridesResponse,
    StageFourResultsRequest,
    StageFourResultsResponse,
)
from src.stage_4_review_results.use_cases import (
    ReviewStateConflictError,
    build_non_conformant_values,
    build_row_context,
    build_stage_four_rows,
    get_review_overrides,
    save_review_overrides,
)
from src.storage import UploadStorage, VersionToken

_MODULE_DIR = FilePath(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"


_templates = templates_for_stage(_TEMPLATE_DIR)


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
    return build_stage_four_rows(
        file_id=payload.file_id,
        upload_storage=storage,
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
    )


DatasetWorkflowIdPath = Annotated[DatasetWorkflowIdField, Path()]


@stage_four_router.get(
    "/overrides/{file_id}",
    response_model=ReviewOverridesSchema | None,
    name="stage_four_get_overrides",
)
async def get_overrides(
    file_id: DatasetWorkflowIdPath,
    response: Response,
) -> ReviewOverridesSchema | None:
    result = get_review_overrides(
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
        file_id=file_id,
    )
    if result is None:
        return None
    response.headers["ETag"] = _review_state_etag(result.version)
    return result.payload


@stage_four_router.post("/overrides", response_model=SaveOverridesResponse, name="stage_four_save_overrides")
async def save_overrides(
    payload: SaveOverridesRequest,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> SaveOverridesResponse:
    storage = dependencies.get_upload_storage()
    expected_version = _review_state_precondition(if_match, if_none_match)
    try:
        result = save_review_overrides(
            workflow_storage=dependencies.get_workflow_storage(),
            user=dependencies.get_user_context(),
            upload_storage=storage,
            file_id=payload.file_id,
            overrides=payload.overrides,
            review_state=payload.review_state,
            expected_version=expected_version,
        )
    except ReviewStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review state changed. Reload this page before saving again.",
        ) from exc
    response.headers["ETag"] = _review_state_etag(result.version)
    return SaveOverridesResponse(file_id=result.file_id, updated_at=result.updated_at)


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
    return build_row_context(
        file_id=payload.file_id,
        row_indices=payload.row_indices,
        upload_storage=storage,
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
    )


def _review_state_etag(version: VersionToken) -> str:
    """Encode the storage token as a standards-compliant opaque HTTP ETag."""
    encoded = base64.urlsafe_b64encode(version.value.encode("utf-8")).decode("ascii")
    return f'"{encoded}"'


def _review_state_precondition(
    if_match: str | None,
    if_none_match: str | None,
) -> VersionToken | None:
    """Validate the create or update precondition and return its version."""
    if (if_match is None) == (if_none_match is None):
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Use If-None-Match: * to create review state or If-Match to update it.",
        )
    if if_none_match is not None:
        if if_none_match != "*":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="If-None-Match must be * when review state is created.",
            )
        return None

    assert if_match is not None
    if len(if_match) < 2 or not if_match.startswith('"') or not if_match.endswith('"'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid review state version.")
    try:
        encoded = if_match[1:-1].encode("ascii")
        value = base64.b64decode(encoded, altchars=b"-_", validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid review state version.") from exc
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid review state version.")
    return VersionToken(value)
