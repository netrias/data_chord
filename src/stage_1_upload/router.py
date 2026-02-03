"""HTTP routes for dataset upload and column analysis."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.domain import ModelSuggestion, get_default_target_schema
from src.domain.data_model_cache import clear_all_session_caches
from src.domain.data_model_client import DataModelClientError
from src.domain.dependencies import (
    get_data_model_client,
    get_mapping_service,
    get_upload_constraints,
    get_upload_storage,
)
from src.domain.manifest import ManifestPayload

from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ColumnPreview,
    DataModelSchema,
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

_upload_constraints = get_upload_constraints()
_storage = get_upload_storage()
_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
_router_logger = logging.getLogger(__name__)

stage_one_router = APIRouter(prefix="/stage-1", tags=["Stage 1 Upload"])


@stage_one_router.get("", response_class=HTMLResponse, name="stage_one_upload_page")
async def render_stage_one(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "ui_constraints": describe_constraints(_upload_constraints),
        "default_schema": get_default_target_schema(),
    }
    return _templates.TemplateResponse("stage_1_upload.html", context)


@stage_one_router.get(
    "/data-models",
    response_model=list[DataModelSchema],
    name="stage_one_data_models",
)
async def list_data_models() -> list[DataModelSchema]:
    """Decouples frontend from model list changes; labels may vary by deployment."""
    client = get_data_model_client()
    try:
        models = await run_in_threadpool(client.list_data_models)
    except DataModelClientError:
        _router_logger.warning("Data Model Store API unavailable")
        raise HTTPException(
            status_code=503,
            detail="Data models are currently unavailable. Please try again later.",
        ) from None
    return [
        DataModelSchema(key=m.key, label=m.label, versions=m.versions)
        for m in models
    ]


@stage_one_router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    name="stage_one_upload_upload",
)
async def upload_dataset(file: Annotated[UploadFile, File(...)]) -> UploadResponse:
    # Clear stale PV/CDE caches from previous single-user sessions
    # TODO: scope to specific file_id when multi-user support is added
    clear_all_session_caches()

    try:
        meta = await _storage.store(file)
    except UnsupportedUploadError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc

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
    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please upload again.")

    total_rows, columns = await run_in_threadpool(_analyze_columns_safe, meta.saved_path, payload.file_id)
    cde_targets, manual_overrides, manifest = await _discover_mappings(
        meta.saved_path, payload.target_schema
    )
    _storage.save_manifest(meta.file_id, manifest)
    _log_analysis_results(total_rows, columns, cde_targets)

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
    )


def _analyze_columns_safe(csv_path: Path, file_id: str) -> tuple[int, list[ColumnPreview]]:
    try:
        return analyze_columns(csv_path)
    except FileNotFoundError as exc:
        _router_logger.exception("Upload missing on disk", extra={"file_id": file_id})
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload missing. Please upload again.") from exc


async def _discover_mappings(
    csv_path: Path,
    target_schema: str,
) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str], ManifestPayload]:
    mapping_service = get_mapping_service()
    try:
        cde_targets, manual_overrides, manifest = await run_in_threadpool(
            mapping_service.discover,
            csv_path=csv_path,
            target_schema=target_schema,
        )
        return cde_targets, manual_overrides, manifest
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        _router_logger.exception(
            "Failed discovering mappings: %s", type(exc).__name__, exc_info=exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch mapping suggestions."
        ) from exc


def _log_analysis_results(
    total_rows: int,
    columns: list[ColumnPreview],
    cde_targets: dict[str, list[ModelSuggestion]],
) -> None:
    cde_target_keys = set(cde_targets)
    missing_columns = [
        col.column_name for col in columns if col.column_name not in cde_target_keys
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
