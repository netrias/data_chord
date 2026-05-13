"""HTTP routes for dataset upload and column analysis."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from netrias_client import DataModelStoreError, NetriasAPIUnavailable

from src.domain import ModelSuggestion
from src.domain.cde import CDEInfo
from src.domain.column_profile import ColumnProfile
from src.domain.data_model_adapter import (
    fetch_all_pvs_async,
    fetch_cdes,
    list_data_model_summaries,
    refine_cde_types_from_pvs,
)
from src.domain.data_model_cache import clear_all_session_caches, get_session_cache
from src.domain.dependencies import (
    get_mapping_service,
    get_upload_constraints,
    get_upload_storage,
)
from src.domain.manifest import ManifestPayload
from src.domain.match_counts import column_value_overlap_ratio
from src.domain.storage import (
    UnsupportedUploadError,
    UploadedFileMeta,
    UploadTooLargeError,
    describe_constraints,
)

from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ColumnOverlapRatio,
    ColumnPreview,
    DataModelSchema,
    DataModelVersionSchema,
    SheetPreview,
    UploadResponse,
)
from .services import analyze_columns, read_workbook_sheet_previews

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
        "default_schema": None,
    }
    return _templates.TemplateResponse("stage_1_upload.html", context)


@stage_one_router.get(
    "/data-models",
    response_model=list[DataModelSchema],
    name="stage_one_data_models",
)
async def list_data_models() -> list[DataModelSchema]:
    """Decouples frontend from model list changes; labels may vary by deployment."""
    try:
        models = await run_in_threadpool(list_data_model_summaries)
    except (DataModelStoreError, NetriasAPIUnavailable):
        _router_logger.warning("Data Model Store API unavailable")
        raise HTTPException(
            status_code=503,
            detail="Data models are currently unavailable. Please try again later.",
        ) from None
    return [
        DataModelSchema(
            key=m.key,
            label=m.label,
            versions=[
                DataModelVersionSchema(
                    version_label=v.version_label,
                    version_number=v.version_number,
                    external_version_number=v.external_version_number,
                    is_default=v.is_default,
                )
                for v in m.versions
            ],
        )
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
        tabular_format=meta.tabular_format.value,
        sheet_names=meta.sheet_names,
        selected_sheet=meta.selected_sheet,
        sheet_previews=await run_in_threadpool(_load_sheet_previews_safe, meta),
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

    meta = _select_sheet_safe(payload.file_id, payload.sheet_name)
    target_version = _target_version(payload.target_version_number)
    analysis_task = asyncio.create_task(
        run_in_threadpool(
            _analyze_columns_safe,
            meta.saved_path,
            payload.file_id,
            meta.selected_sheet,
        )
    )
    discovery_task = asyncio.create_task(
        _discover_mappings(
            meta.saved_path,
            payload.target_schema,
            target_version,
            meta.selected_sheet,
        )
    )
    reference_task = asyncio.create_task(
        _prime_data_model_cache(
            payload.file_id,
            payload.target_schema,
            payload.target_version_number,
        )
    )
    try:
        total_rows, columns, profiles = await analysis_task
        cde_targets, manual_overrides, manifest = await discovery_task
        await reference_task
    except Exception:
        await _cancel_pending_tasks(discovery_task, reference_task)
        raise
    _storage.save_manifest(meta.file_id, manifest)
    # Stash profiles in the session cache so the Stage 2 column-detail endpoint
    # can serve them without re-reading the file.
    cache = get_session_cache(meta.file_id)
    cache.set_column_profiles(profiles)
    column_summaries = _build_column_summaries(
        profiles,
        cde_targets,
        cache.get_all_cdes(),
        cache.get_all_pvs(),
    )
    _log_analysis_results(total_rows, columns, cde_targets)

    return AnalyzeResponse(
        file_id=meta.file_id,
        file_name=meta.original_name,
        target_version_number=payload.target_version_number,
        total_rows=total_rows,
        columns=columns,
        column_summaries=column_summaries,
        cde_targets=cde_targets,
        next_stage="mapping",
        next_step_hint="Review AI-suggested column mappings once ready.",
        manual_overrides=manual_overrides,
        manifest=manifest,
    )


def _select_sheet_safe(file_id: str, sheet_name: str | None) -> UploadedFileMeta:
    try:
        return _storage.select_sheet(file_id, sheet_name)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload not found. Please upload again.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _analyze_columns_safe(
    csv_path: Path, file_id: str, sheet_name: str | None
) -> tuple[int, list[ColumnPreview], dict[str, ColumnProfile]]:
    try:
        return analyze_columns(csv_path, sheet_name=sheet_name)
    except (UnicodeDecodeError, ValueError) as exc:
        _router_logger.warning("Upload failed validation during analysis", extra={"file_id": file_id})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        _router_logger.exception("Upload missing on disk", extra={"file_id": file_id})
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload missing. Please upload again.") from exc


def _load_sheet_previews_safe(meta: UploadedFileMeta) -> dict[str, SheetPreview]:
    if not meta.sheet_names:
        return {}
    try:
        return read_workbook_sheet_previews(meta.saved_path, meta.sheet_names)
    except Exception as exc:
        _router_logger.warning(
            "Worksheet previews unavailable",
            extra={"file_id": meta.file_id, "error": type(exc).__name__},
        )
        return {}


async def _discover_mappings(
    csv_path: Path,
    target_schema: str,
    target_version: str,
    sheet_name: str | None,
) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str], ManifestPayload]:
    mapping_service = get_mapping_service()
    try:
        cde_targets, manual_overrides, manifest = await run_in_threadpool(
            mapping_service.discover,
            csv_path=csv_path,
            target_schema=target_schema,
            target_version=target_version,
            sheet_name=sheet_name,
        )
        return cde_targets, manual_overrides, manifest
    except (UnicodeDecodeError, ValueError) as exc:
        _router_logger.warning("Upload failed validation during analysis", extra={"file_id": csv_path.stem})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        _router_logger.exception(
            "Failed discovering mappings: %s", type(exc).__name__, exc_info=exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch mapping suggestions."
        ) from exc


async def _prime_data_model_cache(file_id: str, data_model_key: str, version_number: int | None) -> None:
    """Warm CDEs and all PVs while mapping discovery is running."""
    version = _target_version(version_number)
    try:
        cdes_task = asyncio.create_task(run_in_threadpool(fetch_cdes, data_model_key, version))
        pvs_task = asyncio.create_task(fetch_all_pvs_async(data_model_key, version))
        cdes, raw_pv_map = await asyncio.gather(cdes_task, pvs_task)
    except (DataModelStoreError, NetriasAPIUnavailable):
        _router_logger.warning("Data Model Store API unavailable during cache warmup", extra={"file_id": file_id})
        return

    cache = get_session_cache(file_id)
    pv_map = {cde.cde_key: raw_pv_map.get(cde.cde_key, frozenset()) for cde in cdes}
    refined = refine_cde_types_from_pvs(cdes, pv_map)
    cache.set_cdes(refined, data_model_key=data_model_key, version_label=version, version_number=version_number)
    cache.set_pvs_batch(pv_map)


def _build_column_summaries(
    profiles: dict[str, ColumnProfile],
    cde_targets: dict[str, list[ModelSuggestion]],
    cdes: list[CDEInfo],
    pv_sets: dict[str, frozenset[str]],
) -> dict[str, ColumnOverlapRatio]:
    """Analyze summaries are keyed by column_key so Stage 2 can render without row scans."""
    cde_by_key = {cde.cde_key: cde for cde in cdes}
    summaries: dict[str, ColumnOverlapRatio] = {}
    for column_key, profile in profiles.items():
        cde = _top_catalog_cde(column_key, cde_targets, cde_by_key)
        distinct = frozenset(dv.value for dv in profile.distinct_values)
        ratio = (
            column_value_overlap_ratio(distinct, cde.cde_type, pv_sets.get(cde.cde_key))
            if cde is not None
            else None
        )
        summaries[column_key] = ColumnOverlapRatio(value_overlap_ratio=ratio)
    return summaries


def _top_catalog_cde(
    column_key: str,
    cde_targets: dict[str, list[ModelSuggestion]],
    cde_by_key: dict[str, CDEInfo],
) -> CDEInfo | None:
    for suggestion in cde_targets.get(column_key, []):
        if cde := cde_by_key.get(suggestion.target):
            return cde
    return None


def _target_version(version_number: int | None) -> str:
    return str(version_number) if version_number is not None else "latest"


async def _cancel_pending_tasks(*tasks: asyncio.Task[object]) -> None:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _log_analysis_results(
    total_rows: int,
    columns: list[ColumnPreview],
    cde_targets: dict[str, list[ModelSuggestion]],
) -> None:
    cde_target_keys = set(cde_targets)
    missing_columns = [
        col.column_key for col in columns if col.column_key not in cde_target_keys
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
