"""Expose HTTP routes for the Stage 1 upload experience."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ColumnPreview,
    DEFAULT_TARGET_SCHEMA,
    HarmonizeRequest,
    HarmonizeResponse,
    ModelSuggestion,
    UploadResponse,
)
from .services import (
    UploadConstraints,
    UploadStorage,
    UploadTooLargeError,
    UnsupportedUploadError,
    analyze_columns,
    describe_constraints,
)
from .harmonize import HarmonizeService
from .mapping_service import MappingDiscoveryService

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_ONE_STATIC_PATH = MODULE_DIR / "static"
UPLOAD_BASE_DIR = MODULE_DIR / "uploads"
MANUAL_MODEL_OPTIONS: tuple[str, ...] = (
    "Primary Diagnosis",
    "Morphology",
    "Sample Anatomic Site",
    "Therapeutic Agents",
    "Tissue or Organ of Origin",
)

_upload_constraints = UploadConstraints(
    allowed_suffixes=(".csv",),
    allowed_content_types=("text/csv", "application/csv", "application/vnd.ms-excel"),
    max_bytes=25 * 1024 * 1024,
)
_storage = UploadStorage(UPLOAD_BASE_DIR, _upload_constraints)
_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
_router_logger = logging.getLogger(__name__)
_mapping_discovery: MappingDiscoveryService | None = None
_harmonizer: HarmonizeService | None = None


def _get_mapping_service() -> MappingDiscoveryService:
    global _mapping_discovery  # noqa: PLW0603 - intentional singleton
    if _mapping_discovery is None:
        _mapping_discovery = MappingDiscoveryService()
    return _mapping_discovery


def _get_harmonizer() -> HarmonizeService:
    global _harmonizer  # noqa: PLW0603 - intentional singleton
    if _harmonizer is None:
        _harmonizer = HarmonizeService()
    return _harmonizer

stage_one_router = APIRouter(prefix="/stage-1", tags=["Stage 1 Upload"])
stage_two_router = APIRouter(tags=["Stage 2 Mapping"])


@stage_one_router.get("", response_class=HTMLResponse, name="stage_one_upload_page")
async def render_stage_one(request: Request) -> HTMLResponse:
    """why: serve the upload UI template."""
    context = {
        "request": request,
        "ui_constraints": describe_constraints(_upload_constraints),
        "default_schema": DEFAULT_TARGET_SCHEMA,
    }
    return _templates.TemplateResponse("stage_1_upload.html", context)


@stage_two_router.get("/stage-2", response_class=HTMLResponse, name="stage_two_mapping_page")
async def render_stage_two(request: Request) -> HTMLResponse:
    """why: serve the mapping review UI."""
    context = {
        "request": request,
        "default_schema": DEFAULT_TARGET_SCHEMA,
        "manual_options": MANUAL_MODEL_OPTIONS,
    }
    return _templates.TemplateResponse("stage_2_mappings.html", context)


@stage_two_router.get("/stage-3", response_class=HTMLResponse, name="stage_three_progress_page")
async def render_stage_three(request: Request) -> HTMLResponse:
    """why: serve the harmonization progress screen."""
    context = {"request": request}
    return _templates.TemplateResponse("stage_3_progress.html", context)


@stage_two_router.get("/stage-4", response_class=HTMLResponse, name="stage_four_placeholder_page")
async def render_stage_four(request: Request) -> HTMLResponse:
    """why: offer a placeholder review stage until implemented."""
    context = {"request": request}
    return _templates.TemplateResponse("stage_4_placeholder.html", context)


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
    "/analyze",
    response_model=AnalyzeResponse,
    name="stage_one_upload_analyze",
)
async def analyze_dataset(payload: AnalyzeRequest) -> AnalyzeResponse:
    """why: run lightweight profiling so the UI can preview columns."""
    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please upload again.")
    total_rows = 0
    columns: list[ColumnPreview] = []
    cde_targets: dict[str, list[ModelSuggestion]] = {}
    try:
        total_rows, columns = analyze_columns(meta.saved_path)
        mapping_service = _get_mapping_service()
        cde_targets, manual_overrides = await run_in_threadpool(
            mapping_service.discover,
            csv_path=meta.saved_path,
            target_schema=payload.target_schema,
        )
    except FileNotFoundError as exc:
        _router_logger.exception("Upload missing on disk", extra={"file_id": payload.file_id})
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload missing. Please upload again.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        _router_logger.exception("Failed discovering mappings", exc_info=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch mapping suggestions.") from exc

    missing_columns = [
        column.column_name
        for column in columns
        if column.column_name not in cde_targets
        and column.column_name.lower() not in cde_targets
    ]
    _router_logger.info(
        "Analyze completed",
        extra={
            "total_rows": total_rows,
            "column_count": len(columns),
            "mapped_columns": len(cde_targets),
            "missing_columns": missing_columns[:10],
            "missing_count": len(missing_columns),
        },
    )

    return AnalyzeResponse(
        file_id=meta.file_id,
        file_name=meta.original_name,
        total_rows=total_rows,
        columns=columns,
        cde_targets=cde_targets,
        next_stage="mapping",
        next_step_hint="Review AI-suggested column mappings once ready.",
        manual_overrides=manual_overrides,
    )


@stage_two_router.post(
    "/stage-2/harmonize",
    response_model=HarmonizeResponse,
    name="stage_two_harmonize",
)
async def harmonize_dataset(payload: HarmonizeRequest) -> HarmonizeResponse:
    """why: trigger harmonization using the Netrias client."""
    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please rerun analysis.")

    harmonizer = _get_harmonizer()
    result = harmonizer.run(
        file_path=meta.saved_path,
        target_schema=payload.target_schema,
        manual_overrides=payload.manual_overrides,
    )
    next_stage_url = f"/stage-3?job_id={result.job_id}&status={result.status}&detail={result.detail}"
    return HarmonizeResponse(
        job_id=result.job_id,
        status=result.status,
        detail=result.detail,
        next_stage_url=next_stage_url,
    )
