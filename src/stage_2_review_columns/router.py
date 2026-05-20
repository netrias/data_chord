"""
HTTP routes for displaying and confirming column-to-CDE mappings.

Coordinates CDE discovery API calls and browser state for Stage 2.
"""

from __future__ import annotations

import logging
from pathlib import Path as _Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from netrias_client import DataModelStoreError, NetriasAPIUnavailable

import src.domain.dependencies as dependencies
from src.domain import UILabel
from src.domain.cde import CDEInfo
from src.domain.data_model_cache import get_session_cache, populate_cde_cache
from src.domain.data_model_selection import DataModelSelection
from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN
from src.domain.workflow_state_store import load_workflow_state

from .schemas import ColumnDetailResponse, SaveMappingChoicesRequest, SaveMappingChoicesResponse
from .use_cases import (
    ColumnDetailNotFound,
    MappingWorkflowStateConflictError,
    MappingWorkflowStateNotFoundError,
    compute_column_detail,
    save_confirmed_mapping_choices,
)

MODULE_DIR = _Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
logger = logging.getLogger(__name__)

stage_two_router = APIRouter(tags=["Stage 2 Mapping"])


@stage_two_router.get("/stage-2", response_class=HTMLResponse, name="stage_two_mapping_page")
async def render_stage_two(
    request: Request,
    file_id: Annotated[str | None, Query(min_length=8, pattern=r"^[a-f0-9]+$")] = None,
    schema: Annotated[str | None, Query(min_length=1)] = None,
    version_number: Annotated[int | None, Query(ge=1)] = None,
) -> HTMLResponse:
    cde_catalog: list[CDEInfo] = []
    selection = _data_model_selection_for_request(file_id, schema, version_number)

    if file_id and selection:
        cde_catalog = await _get_cde_options_for_session(file_id, selection)

    context = {
        "request": request,
        "default_schema": selection.key if selection else schema or "",
        "default_version_number": selection.version_number if selection else version_number,
        "cde_catalog": [_cde_catalog_item(cde) for cde in cde_catalog],
        "no_mapping_label": UILabel.NO_MAPPING.value,
    }
    return _templates.TemplateResponse("stage_2_mappings.html", context)


def _data_model_selection_for_request(
    file_id: str | None,
    target_schema: str | None,
    version_number: int | None,
) -> DataModelSelection | None:
    if file_id:
        state = load_workflow_state(
            dependencies.get_workflow_storage(),
            dependencies.get_user_context(),
            file_id,
        )
        if state is not None:
            return state.data_model_selection
    if target_schema is None:
        return None
    return DataModelSelection.from_version_number(target_schema, version_number)


async def _get_cde_options_for_session(
    file_id: str,
    selection: DataModelSelection,
) -> list[CDEInfo]:
    """Returns empty list on API failure (graceful degradation)."""
    cache = get_session_cache(file_id)

    if not cache.has_cdes():
        try:
            await run_in_threadpool(populate_cde_cache, file_id, selection)
        except (DataModelStoreError, NetriasAPIUnavailable):
            logger.warning("Data Model Store API unavailable; CDE options will be empty", extra={"file_id": file_id})

    return cache.get_all_cdes()


def _cde_catalog_item(cde: CDEInfo) -> dict[str, object]:
    """Project the domain CDE into the small browser picker payload."""
    return {
        "cde_id": cde.cde_id,
        "cde_key": cde.cde_key,
        "description": cde.description or "",
        "cde_type": cde.cde_type.value,
    }


@stage_two_router.get(
    "/stage-2/column-detail/{file_id}/{column_key}",
    response_model=ColumnDetailResponse,
    name="stage_two_column_detail",
)
async def get_column_detail(
    file_id: Annotated[str, Path(min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)],
    column_key: Annotated[str, Path(min_length=1)],
    selected_cde_key: Annotated[str | None, Query(min_length=1)] = None,
) -> ColumnDetailResponse:
    """Returns match counts, CDE types, and selected PVs for one column."""
    try:
        return await compute_column_detail(file_id, column_key, selected_cde_key)
    except ColumnDetailNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@stage_two_router.post(
    "/stage-2/choices",
    response_model=SaveMappingChoicesResponse,
    name="stage_two_save_mapping_choices",
)
async def save_mapping_choices(payload: SaveMappingChoicesRequest) -> SaveMappingChoicesResponse:
    try:
        return save_confirmed_mapping_choices(
            workflow_storage=dependencies.get_workflow_storage(),
            user=dependencies.get_user_context(),
            payload=payload,
        )
    except MappingWorkflowStateNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow state not found. Please rerun analysis.",
        ) from exc
    except MappingWorkflowStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow state changed. Please refresh and try again.",
        ) from exc
