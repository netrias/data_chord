"""Serve Stage 3 harmonization routes and assets."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.stage_1_upload.dependencies import get_harmonize_service, get_upload_storage
from src.stage_1_upload.schemas import (
    DEFAULT_TARGET_SCHEMA,
    HarmonizeRequest,
    HarmonizeResponse,
    ManifestPayload,
)

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_THREE_STATIC_PATH = MODULE_DIR / "static"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
_storage = get_upload_storage()
_router_logger = logging.getLogger(__name__)

stage_three_router = APIRouter(prefix="/stage-3", tags=["Stage 3 Harmonize"])


@stage_three_router.get("", response_class=HTMLResponse, name="stage_three_entry")
async def render_stage_three(request: Request) -> HTMLResponse:
    """why: serve the harmonization progress UI."""

    context = {
        "request": request,
        "default_schema": DEFAULT_TARGET_SCHEMA,
        "next_stage_url": "/stage-4",
    }
    return _templates.TemplateResponse("stage_3_harmonize.html", context)


@stage_three_router.post(
    "/harmonize",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize",
)
async def harmonize_dataset(payload: HarmonizeRequest) -> HarmonizeResponse:
    """why: trigger harmonization using the Netrias client."""

    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please rerun analysis.")

    stored_manifest = _storage.load_manifest(payload.file_id)
    manifest_payload = payload.manifest or cast(ManifestPayload | None, stored_manifest)
    harmonizer = get_harmonize_service()
    result = await run_in_threadpool(
        harmonizer.run,
        file_path=meta.saved_path,
        target_schema=payload.target_schema,
        manual_overrides=payload.manual_overrides,
        manifest=manifest_payload,
    )
    _router_logger.info(
        "Harmonization job dispatched",
        extra={"file_id": payload.file_id, "job_id": result.job_id, "status": result.status},
    )
    next_stage_url = f"/stage-4?job_id={result.job_id}&status={result.status}&detail={result.detail}"
    return HarmonizeResponse(
        job_id=result.job_id,
        status=result.status,
        detail=result.detail,
        next_stage_url=next_stage_url,
        job_id_available=result.job_id_available,
    )


__all__ = ["stage_three_router", "STAGE_THREE_STATIC_PATH"]
