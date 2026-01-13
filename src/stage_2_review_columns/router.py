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
from src.domain.config import get_data_model_key
from src.domain.data_model_cache import get_session_cache
from src.domain.dependencies import get_data_model_client

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
logger = logging.getLogger(__name__)

stage_two_router = APIRouter(tags=["Stage 2 Mapping"])


@stage_two_router.get("/stage-2", response_class=HTMLResponse, name="stage_two_mapping_page")
async def render_stage_two(
    request: Request,
    file_id: Annotated[str | None, Query()] = None,
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
        try:
            client = get_data_model_client()
            data_model_key = get_data_model_key()

            version_label = await run_in_threadpool(client.get_latest_version, data_model_key)
            cdes = await run_in_threadpool(client.fetch_cdes, data_model_key, version_label)

            cache.set_cdes(cdes, data_model_key, version_label)
            logger.info(
                "Fetched CDEs for session",
                extra={"file_id": file_id, "cde_count": len(cdes), "version": version_label},
            )
        except Exception:
            logger.exception("Failed to fetch CDEs from Data Model Store", extra={"file_id": file_id})
            return []

    return [
        {
            "cde_id": cde.cde_id,
            "cde_key": cde.cde_key,
            "label": cde.cde_key,
            "description": cde.description or "",
        }
        for cde in cache.get_all_cdes()
    ]

