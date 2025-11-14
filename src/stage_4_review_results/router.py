"""Serve Stage 4 review and approval routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.stage_1_upload.schemas import DEFAULT_TARGET_SCHEMA

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_FOUR_STATIC_PATH = MODULE_DIR / "static"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

stage_four_router = APIRouter(prefix="/stage-4", tags=["Stage 4 Review"])


@stage_four_router.get("", response_class=HTMLResponse, name="stage_four_review_page")
async def render_stage_four(request: Request) -> HTMLResponse:
    """why: serve the batch review and approval UI."""

    context = {
        "request": request,
        "default_schema": DEFAULT_TARGET_SCHEMA,
        "stage_three_payload_key": "stage3HarmonizePayload",
        "stage_three_job_key": "stage3HarmonizeJob",
        "stage_two_url": request.url_for("stage_two_mapping_page"),
        "stage_three_url": request.url_for("stage_three_entry"),
    }
    return _templates.TemplateResponse("stage_4_review.html", context)


__all__ = ["stage_four_router", "STAGE_FOUR_STATIC_PATH"]
