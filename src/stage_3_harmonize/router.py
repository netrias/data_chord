"""Serve Stage 3 harmonization routes and assets."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.domain import DEFAULT_TARGET_SCHEMA, ColumnMappingSet
from src.domain.dependencies import get_harmonize_service, get_upload_storage
from src.domain.manifest import ManifestPayload, ManifestSummary, read_manifest_parquet
from src.stage_1_upload.schemas import (
    HarmonizeRequest,
    HarmonizeResponse,
    ManifestRowSchema,
    ManifestSummarySchema,
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
    column_mappings = ColumnMappingSet.from_dict(payload.manual_overrides)
    harmonizer = get_harmonize_service()
    result = await run_in_threadpool(
        harmonizer.run,
        file_path=meta.saved_path,
        target_schema=payload.target_schema,
        column_mappings=column_mappings,
        manifest=manifest_payload,
    )
    _router_logger.info(
        "Harmonization job dispatched",
        extra={"file_id": payload.file_id, "job_id": result.job_id, "status": result.status},
    )

    manifest_summary = _read_and_store_manifest(payload.file_id, result.manifest_path)

    query_params = urlencode({
        "file_id": payload.file_id,
        "job_id": result.job_id,
        "status": result.status,
        "detail": result.detail or "",
    })
    next_stage_url = f"/stage-4?{query_params}"
    return HarmonizeResponse(
        job_id=result.job_id,
        status=result.status,
        detail=result.detail,
        next_stage_url=next_stage_url,
        job_id_available=result.job_id_available,
        manifest_summary=manifest_summary,
    )


MANIFEST_PREVIEW_LIMIT: int = 100


def _read_and_store_manifest(file_id: str, manifest_path: Path | None) -> ManifestSummarySchema | None:
    """why: read manifest parquet and store for later stages."""
    if manifest_path is None or not manifest_path.exists():
        return None

    manifest_data = read_manifest_parquet(manifest_path)
    if manifest_data is None:
        return None

    _ = _storage.save_harmonization_manifest(file_id, manifest_path)
    return _convert_to_schema(manifest_data)


def _convert_to_schema(manifest: ManifestSummary) -> ManifestSummarySchema:
    """why: transform internal manifest data into API response schema."""
    preview_rows = [
        ManifestRowSchema(
            column_name=row.column_name,
            to_harmonize=row.to_harmonize,
            top_harmonization=row.top_harmonization,
            confidence_score=row.confidence_score,
            row_indices=row.row_indices,
        )
        for row in manifest.rows[:MANIFEST_PREVIEW_LIMIT]
    ]
    return ManifestSummarySchema(
        total_terms=manifest.total_terms,
        changed_terms=manifest.changed_terms,
        high_confidence_count=manifest.high_confidence_count,
        medium_confidence_count=manifest.medium_confidence_count,
        low_confidence_count=manifest.low_confidence_count,
        preview_rows=preview_rows,
    )


__all__ = ["stage_three_router", "STAGE_THREE_STATIC_PATH"]
