"""Expose HTTP routes for the Stage 1 upload experience."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.domain import DEFAULT_TARGET_SCHEMA, ModelSuggestion
from src.domain.dependencies import get_mapping_service, get_upload_constraints, get_upload_storage
from src.domain.manifest import ManifestPayload

from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ColumnPreview,
    PreviewRequest,
    PreviewResponse,
    UploadResponse,
)
from .services import (
    UnsupportedUploadError,
    UploadTooLargeError,
    analyze_columns,
    describe_constraints,
)

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_ONE_STATIC_PATH = MODULE_DIR / "static"

_upload_constraints = get_upload_constraints()
_storage = get_upload_storage()
_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
_router_logger = logging.getLogger(__name__)

stage_one_router = APIRouter(prefix="/stage-1", tags=["Stage 1 Upload"])


@stage_one_router.get("", response_class=HTMLResponse, name="stage_one_upload_page")
async def render_stage_one(request: Request) -> HTMLResponse:
    """why: serve the upload UI template."""
    context = {
        "request": request,
        "ui_constraints": describe_constraints(_upload_constraints),
        "default_schema": DEFAULT_TARGET_SCHEMA,
    }
    return _templates.TemplateResponse("stage_1_upload.html", context)


@stage_one_router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    name="stage_one_upload_upload",
)
async def upload_dataset(file: Annotated[UploadFile, File(...)]) -> UploadResponse:
    """why: accept a CSV file and persist it for subsequent analysis."""
    try:
        meta = await _storage.store(file)
    except UnsupportedUploadError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc

    return UploadResponse(
        file_id=meta.file_id,
        file_name=meta.original_name,
        human_size=meta.human_size,
        content_type=meta.content_type,
        uploaded_at=meta.uploaded_at,
    )


@stage_one_router.post(
    "/preview",
    response_model=PreviewResponse,
    name="stage_one_upload_preview",
)
async def preview_columns(payload: PreviewRequest) -> PreviewResponse:
    """why: return lightweight column metadata for Stage 2 animation without AI mapping."""
    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please upload again.")

    total_rows, columns = _analyze_columns_safe(meta.saved_path, payload.file_id)

    return PreviewResponse(
        file_id=meta.file_id,
        file_name=meta.original_name,
        total_rows=total_rows,
        columns=columns,
    )


@stage_one_router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    name="stage_one_upload_analyze",
)
async def analyze_dataset(payload: AnalyzeRequest) -> AnalyzeResponse:
    """why: run lightweight profiling so the UI can preview columns."""
    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please upload again.")

    total_rows, columns = _analyze_columns_safe(meta.saved_path, payload.file_id)
    cde_targets, manual_overrides, manifest, mapping_available = await _discover_mappings(
        meta.saved_path, payload.target_schema
    )
    _storage.save_manifest(meta.file_id, manifest)
    _log_analysis_results(total_rows, columns, cde_targets, mapping_available)

    return AnalyzeResponse(
        file_id=meta.file_id,
        file_name=meta.original_name,
        total_rows=total_rows,
        columns=columns,
        cde_targets=cde_targets,
        next_stage="mapping",
        next_step_hint="Review AI-suggested column mappings once ready.",
        manual_overrides=manual_overrides,
        manifest=manifest,
        mapping_service_available=mapping_available,
    )


def _analyze_columns_safe(csv_path: Path, file_id: str) -> tuple[int, list[ColumnPreview]]:
    """why: wrap column analysis with error handling."""
    try:
        return analyze_columns(csv_path)
    except FileNotFoundError as exc:
        _router_logger.exception("Upload missing on disk", extra={"file_id": file_id})
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload missing. Please upload again.") from exc


async def _discover_mappings(
    csv_path: Path,
    target_schema: str,
) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str], ManifestPayload, bool]:
    """why: fetch CDE mapping suggestions from the mapping service."""
    mapping_service = get_mapping_service()
    mapping_available = mapping_service.available()
    try:
        cde_targets, manual_overrides, manifest = await run_in_threadpool(
            mapping_service.discover,
            csv_path=csv_path,
            target_schema=target_schema,
        )
        return cde_targets, manual_overrides, manifest, mapping_available
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        _router_logger.exception("Failed discovering mappings", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch mapping suggestions."
        ) from exc


def _log_analysis_results(
    total_rows: int,
    columns: list[ColumnPreview],
    cde_targets: dict[str, list[ModelSuggestion]],
    mapping_available: bool,
) -> None:
    """why: log analysis completion with summary metrics."""
    cde_target_keys = {k.lower() for k in cde_targets}
    missing_columns = [
        col.column_name for col in columns if col.column_name.lower() not in cde_target_keys
    ]
    _router_logger.info(
        "Analyze completed",
        extra={
            "total_rows": total_rows,
            "column_count": len(columns),
            "mapped_columns": len(cde_targets),
            "missing_columns": missing_columns[:10],
            "missing_count": len(missing_columns),
            "mapping_service_available": mapping_available,
        },
    )
