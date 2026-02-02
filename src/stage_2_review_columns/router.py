"""
HTTP routes for displaying and confirming column-to-CDE mappings.

Coordinates CDE discovery API calls and browser state for Stage 2.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.domain import UILabel, get_default_target_schema
from src.domain.data_model_cache import get_session_cache
from src.domain.data_model_client import DataModelClientError
from src.domain.demo_bypass import inject_demo_cdes_into_cache
from src.domain.dependencies import get_data_model_client

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
logger = logging.getLogger(__name__)

stage_two_router = APIRouter(tags=["Stage 2 Mapping"])


@stage_two_router.get("/stage-2", response_class=HTMLResponse, name="stage_two_mapping_page")
async def render_stage_two(
    request: Request,
    file_id: Annotated[str | None, Query(min_length=8, pattern=r"^[a-f0-9]+$")] = None,
) -> HTMLResponse:
    cde_options: list[dict[str, object]] = []

    if file_id:
        cde_options = await _get_cde_options_for_session(file_id)

    context = {
        "request": request,
        "default_schema": get_default_target_schema(),
        "cde_options": cde_options,
        "no_mapping_label": UILabel.NO_MAPPING.value,
    }
    return _templates.TemplateResponse("stage_2_mappings.html", context)


async def _get_cde_options_for_session(file_id: str) -> list[dict[str, object]]:
    """Returns empty list on API failure (graceful degradation)."""
    cache = get_session_cache(file_id)

    if not cache.has_cdes():
        # TEMPORARY DEMO BYPASS: Injects hardcoded CDEs instead of fetching from
        # Data Model Store API. Remove when CDE ID API is stable. See demo_bypass.py.
        # Production code: see git 6039810 for the real fetch_cdes path.
        client = get_data_model_client()
        try:
            await run_in_threadpool(inject_demo_cdes_into_cache, file_id, client)
        except DataModelClientError:
            logger.warning("Data Model Store API unavailable; CDE options will be empty", extra={"file_id": file_id})

    return [
        {
            "cde_id": cde.cde_id,
            "cde_key": cde.cde_key,
            "label": cde.cde_key,
            "description": cde.description or "",
        }
        for cde in cache.get_all_cdes()
    ]

