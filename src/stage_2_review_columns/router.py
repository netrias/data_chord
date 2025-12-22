"""Expose HTTP routes for reviewing and confirming column mappings."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.domain import DEFAULT_TARGET_SCHEMA, UILabel, get_cde_labels

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_TWO_STATIC_PATH = MODULE_DIR / "static"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

stage_two_router = APIRouter(tags=["Stage 2 Mapping"])


@stage_two_router.get("/stage-2", response_class=HTMLResponse, name="stage_two_mapping_page")
async def render_stage_two(request: Request) -> HTMLResponse:
    """why: serve the mapping review UI."""
    context = {
        "request": request,
        "default_schema": DEFAULT_TARGET_SCHEMA,
        "manual_options": get_cde_labels(),
        "no_mapping_label": UILabel.NO_MAPPING.value,
    }
    return _templates.TemplateResponse("stage_2_mappings.html", context)

