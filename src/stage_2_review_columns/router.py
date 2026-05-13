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

from src.domain import UILabel
from src.domain.data_model_cache import get_session_cache, populate_cde_cache
from src.domain.data_model_selection import DataModelSelection
from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN

from .schemas import CdeCatalogItem, ColumnDetailResponse
from .services import ColumnDetailNotFound, compute_column_detail

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
    cde_catalog: list[CdeCatalogItem] = []

    if file_id and schema:
        cde_catalog = await _get_cde_options_for_session(file_id, schema, version_number)

    context = {
        "request": request,
        "default_schema": schema or "",
        "default_version_number": version_number,
        "cde_catalog": [item.model_dump() for item in cde_catalog],
        "no_mapping_label": UILabel.NO_MAPPING.value,
    }
    return _templates.TemplateResponse("stage_2_mappings.html", context)


async def _get_cde_options_for_session(
    file_id: str,
    target_schema: str,
    version_number: int | None,
) -> list[CdeCatalogItem]:
    """Returns empty list on API failure (graceful degradation)."""
    cache = get_session_cache(file_id)

    if not cache.has_cdes():
        try:
            selection = DataModelSelection.from_version_number(target_schema, version_number)
            await run_in_threadpool(populate_cde_cache, file_id, selection)
        except (DataModelStoreError, NetriasAPIUnavailable):
            logger.warning("Data Model Store API unavailable; CDE options will be empty", extra={"file_id": file_id})

    return [
        CdeCatalogItem(
            cde_id=cde.cde_id,
            cde_key=cde.cde_key,
            label=cde.cde_key,
            description=cde.description or "",
            cde_type=cde.cde_type.value,
        )
        for cde in cache.get_all_cdes()
    ]


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
