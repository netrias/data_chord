"""
HTTP routes for triggering harmonization and building result summaries.

Orchestrates parallel harmonization and PV fetch tasks.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from netrias_client import read_tabular, write_tabular

import src.app.dependencies as dependencies
from src.api.schemas import (
    HarmonizeRequest,
    HarmonizeResponse,
)
from src.app.data_model_store import fetch_all_pvs_async, populate_cde_cache
from src.app.dependencies import (
    get_harmonize_service,
)
from src.app.session_cache import SessionCache, get_session_cache
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.cde_type_classification import refine_cde_types_from_pvs
from src.domain.column_cde_map import ColumnCdeMap
from src.domain.column_outcomes import (
    ColumnOutcome,
    FinalizedValueOutcome,
    FinalValueSource,
    summarize_column_outcomes,
)
from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import ColumnKey
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import (
    ConfidenceBucketCount,
    HarmonizationColumnBreakdown,
    HarmonizationManifestSummary,
    HarmonizeStatus,
)
from src.domain.manifest import (
    ColumnMappingManifest,
    ConfidenceBucket,
    ManifestPvAdjustment,
    ManifestRow,
    ManifestSummary,
    confidence_bucket,
)
from src.domain.pv_validation import check_value_conformance, compute_pv_adjustment
from src.domain.tabular_column_renames import (
    ResolvedTabularColumn,
    apply_column_renames_to_dataset,
    resolve_tabular_columns,
)
from src.integrations.netrias_harmonize import HarmonizeResult
from src.persistence.cde_mapping_document_store import save_cde_mapping_document
from src.persistence.harmonization_job_store import HarmonizationJobState
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_writer import apply_column_renames_batch, apply_pv_adjustments_batch
from src.persistence.pv_manifest_store import ColumnPvSets, save_pv_snapshot
from src.persistence.review_override_store import delete_review_overrides_state
from src.persistence.workflow_artifacts import (
    load_upload_artifact,
    save_harmonized_artifacts,
)
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.stage_3_harmonize.use_cases import (
    HarmonizationStart,
    HarmonizationStartConflictError,
    HarmonizationWorkflowNotFoundError,
    HarmonizationWorkflowUnreadableError,
    RunAuthority,
    StaleStageThreeWorkerError,
    complete_stage_three_job,
    fail_stage_three_job,
    heartbeat_stage_three_job,
    load_authorized_job,
    start_harmonization,
)
from src.storage import UploadStorage, UserContext, WorkflowFile, WorkflowNotFoundError, WorkflowStorage

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
NEXT_STAGE_PATH = "/stage-4"
JOB_START_GRACE_SECONDS = 0.25

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
_router_logger = logging.getLogger(__name__)

stage_three_router = APIRouter(prefix="/stage-3", tags=["Stage 3 Harmonize"])


_stage_three_tasks: dict[str, asyncio.Task[None]] = {}


class ColumnStats(NamedTuple):
    total_rows: int
    changed_rows: int
    unique_terms_changed: int
    non_conformant_terms: int
    confidence_counts: dict[ConfidenceBucket, int]


@stage_three_router.get("", response_class=HTMLResponse, name="stage_three_entry")
async def render_stage_three(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "next_stage_url": NEXT_STAGE_PATH,
    }
    return _templates.TemplateResponse(request, "stage_3_harmonize.html", context)


@stage_three_router.post(
    "/harmonize",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize",
)
async def harmonize_dataset(payload: HarmonizeRequest) -> HarmonizeResponse:
    storage = dependencies.get_upload_storage()
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        start = start_harmonization(
            upload_storage=storage,
            workflow_storage=workflow_storage,
            user=user,
            payload=payload,
        )
    except HarmonizationWorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found. Please rerun analysis.",
        ) from exc
    except (HarmonizationStartConflictError, HarmonizationWorkflowUnreadableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow state changed or is unreadable. Please refresh and try again.",
        ) from exc

    if not start.should_run:
        return _response_from_job(start.loaded_job.job)

    job = start.loaded_job.job
    task = asyncio.create_task(_run_stage_three_job(start, payload, workflow_storage, user))
    _stage_three_tasks[job.polling_job_id] = task
    task.add_done_callback(lambda _task: _stage_three_tasks.pop(job.polling_job_id, None))
    try:
        # Fast mocked jobs should return their final state immediately, while
        # real SDK jobs fall through to browser polling without blocking the POST.
        await asyncio.wait_for(asyncio.shield(task), timeout=JOB_START_GRACE_SECONDS)
    except TimeoutError:
        pass
    loaded = load_authorized_job(
        workflow_storage=workflow_storage,
        user=user,
        file_id=payload.file_id,
        requested_job_id=job.polling_job_id,
    )
    return _response_from_job(loaded.job if loaded is not None else job)


@stage_three_router.get(
    "/jobs/{job_id}",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize_job",
)
async def get_harmonize_job(job_id: str, file_id: str | None = Query(default=None)) -> HarmonizeResponse:
    if file_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Harmonization job not found.")
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        loaded = load_authorized_job(
            workflow_storage=workflow_storage,
            user=user,
            file_id=file_id,
            requested_job_id=job_id,
        )
    except (HarmonizationStartConflictError, HarmonizationWorkflowUnreadableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Harmonization state changed or is unreadable. Please refresh.",
        ) from exc
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Harmonization job not found.")
    return _response_from_job(loaded.job)


async def _run_stage_three_job(
    start: HarmonizationStart,
    payload: HarmonizeRequest,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> None:
    accepted_job = start.loaded_job.job
    stop_heartbeat = asyncio.Event()
    heartbeat = asyncio.create_task(
        heartbeat_stage_three_job(
            workflow_storage=workflow_storage,
            user=user,
            accepted_job=accepted_job,
            stop=stop_heartbeat,
        )
    )
    try:
        response = await _run_harmonization_workflow(
            payload,
            start.loaded_state,
            RunAuthority(workflow_storage, user, accepted_job),
        )
        complete_stage_three_job(
            workflow_storage=workflow_storage,
            user=user,
            accepted_job=accepted_job,
            response=response,
        )
    except StaleStageThreeWorkerError:
        _router_logger.warning(
            "Superseded Stage 3 worker stopped before publishing",
            extra={"file_id": payload.file_id, "job_id": accepted_job.polling_job_id},
        )
        fail_stage_three_job(
            workflow_storage=workflow_storage,
            user=user,
            accepted_job=accepted_job,
        )
    except Exception:  # pragma: no cover - defensive job boundary
        _router_logger.exception("Stage 3 background harmonization failed", extra={"file_id": payload.file_id})
        fail_stage_three_job(
            workflow_storage=workflow_storage,
            user=user,
            accepted_job=accepted_job,
        )
    finally:
        stop_heartbeat.set()
        await heartbeat


def _response_from_job(job: HarmonizationJobState) -> HarmonizeResponse:
    return HarmonizeResponse(
        job_id=job.job_id,
        status=job.status,
        detail=job.detail,
        next_stage_url=_next_stage_url(
            file_id=job.file_id,
            job_id=job.job_id,
            job_status=job.status,
        ),
        job_id_available=job.job_id_available,
        elapsed_seconds=job.elapsed_seconds(),
        manifest_summary=job.manifest_summary,
    )


async def _run_harmonization_workflow(
    payload: HarmonizeRequest,
    loaded_state: LoadedWorkflowState,
    run_authority: RunAuthority,
) -> HarmonizeResponse:
    storage = dependencies.get_upload_storage()
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    meta = load_upload_artifact(storage, workflow_storage, user, payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please rerun analysis.")

    delete_review_overrides_state(
        workflow_storage,
        user,
        payload.file_id,
    )
    try:
        # A fresh harmonization invalidates any Stage 4 edits and mapping audit
        # generated from the previous manifest.
        workflow_storage.delete_json(
            user,
            payload.file_id,
            WorkflowFile.CDE_MAPPING,
        )
    except WorkflowNotFoundError:
        pass
    workflow_state = loaded_state.state
    manifest = workflow_state.mapping_manifest
    mapping_choices = workflow_state.mapping_choices
    if manifest is None or mapping_choices is None:
        raise ValueError("Workflow mapping choices are incomplete")
    column_overrides = mapping_choices.column_overrides
    column_renames = mapping_choices.column_renames
    data_model_version = workflow_state.data_model_version
    resolved_columns = await _resolved_columns_for_source(
        meta.saved_path,
        column_renames,
        meta.selected_sheet,
    )

    cache = get_session_cache(payload.file_id, owner_user_id=user.user_id)
    await _ensure_reference_catalog(
        cache,
        payload.file_id,
        data_model_version,
        user.user_id,
    )
    prepared_manifest = manifest.apply_choices(column_overrides, column_renames, cache.get_cde_catalog())
    column_cde_map = prepared_manifest.column_cde_map()
    output_path = storage.harmonized_path_for(payload.file_id, meta.saved_path)

    harmonize_task = asyncio.create_task(
        _run_harmonization(
            meta.saved_path,
            data_model_version,
            prepared_manifest,
            output_path,
            meta.selected_sheet,
        )
    )
    pv_fetch_task = asyncio.create_task(
        _fetch_pvs_for_session(
            payload.file_id,
            cache,
            column_cde_map,
            data_model_version,
        )
    )

    # Fetch PVs beside harmonization so Stage 4 can validate values without
    # adding another user-visible wait after the SDK call finishes.
    result, pv_catalog = await asyncio.gather(harmonize_task, pv_fetch_task)
    if result.status == HarmonizeStatus.SUCCEEDED:
        # Provider output is still scratch at this point. Refuse to transform
        # or publish it if this worker or its workflow plan was superseded.
        run_authority.require_current()
        run_authority.require_plan_current()
    harmonized_output_path = result.output_path or output_path
    if result.status == HarmonizeStatus.SUCCEEDED:
        await _apply_column_renames_to_output(
            harmonized_output_path,
            column_renames,
            meta.selected_sheet,
        )
        harmonized_output_path = await _refresh_managed_harmonized_output(
            harmonized_output_path,
            output_path,
        )

    _router_logger.info(
        "Harmonization job dispatched",
        extra={
            "file_id": payload.file_id,
            "job_id": result.job_id,
            "status": result.status,
            "manifest_path": str(result.manifest_path),
            "manifest_path_exists": result.manifest_path.exists() if result.manifest_path else False,
        },
    )

    manifest_summary = await _read_store_and_adjust_manifest(
        payload.file_id,
        result.manifest_path,
        storage,
        column_renames,
        ColumnPvSets({
            column_key: pv_catalog.get(cde_key)
            for column_key, cde_key in column_cde_map.mappings.items()
        }),
    )
    if result.status == HarmonizeStatus.SUCCEEDED:
        if not harmonized_output_path.exists() or manifest_summary is None:
            raise RuntimeError("Harmonization completed without required output artifacts")
        run_authority.require_current()
        run_authority.require_plan_current()
        save_pv_snapshot(workflow_storage, user, loaded_state, pv_catalog)
        save_cde_mapping_document(
            workflow_storage,
            user,
            payload.file_id,
            manifest,
            column_overrides,
            column_renames,
            resolved_columns,
            cache,
            data_model_version,
        )
        save_harmonized_artifacts(
            workflow_storage,
            user,
            payload.file_id,
            harmonized_output_path,
            storage.load_harmonization_manifest_path(payload.file_id),
        )
    _router_logger.info(
        "Manifest summary result",
        extra={"file_id": payload.file_id, "has_summary": manifest_summary is not None},
    )

    return HarmonizeResponse(
        job_id=result.job_id,
        status=result.status,
        detail=result.detail,
        next_stage_url=_next_stage_url(
            file_id=payload.file_id,
            job_id=result.job_id,
            job_status=result.status,
        ),
        job_id_available=result.job_id_available,
        manifest_summary=manifest_summary,
    )


def _next_stage_url(*, file_id: str, job_id: str, job_status: HarmonizeStatus) -> str:
    query_params = urlencode({
        "file_id": file_id,
        "job_id": job_id,
        "status": job_status.value,
        "detail": "",
    })
    return f"{NEXT_STAGE_PATH}?{query_params}"


async def _run_harmonization(
    file_path: Path,
    data_model_version: DataModelVersionReference,
    prepared_manifest: ColumnMappingManifest,
    output_path: Path,
    sheet_name: str | None,
) -> HarmonizeResult:
    """Netrias client is sync; run in threadpool to avoid blocking the event loop."""
    harmonizer = get_harmonize_service()
    return await run_in_threadpool(
        harmonizer.run,
        file_path=file_path,
        data_model_key=data_model_version.data_model_key,
        external_version_number=data_model_version.external_version_number,
        prepared_manifest=prepared_manifest,
        output_path=output_path,
        sheet_name=sheet_name,
    )


async def _ensure_reference_catalog(
    cache: SessionCache,
    file_id: str,
    data_model_version: DataModelVersionReference,
    owner_user_id: str,
) -> None:
    if cache.get_data_model_version() == data_model_version and cache.has_cdes():
        return
    await run_in_threadpool(
        populate_cde_cache,
        file_id,
        data_model_version,
        owner_user_id=owner_user_id,
    )


async def _fetch_and_cache_pvs(
    cache: SessionCache,
    data_model_version: DataModelVersionReference,
    cde_keys: list[str],
    file_id: str,
) -> CdePvCatalog:
    _router_logger.info(
        "Fetching PVs from Data Model Store",
        extra={
            "file_id": file_id,
            "data_model_key": data_model_version.data_model_key,
            "external_version_number": data_model_version.external_version_number,
            "cde_keys": cde_keys,
        },
    )
    pv_catalog = (
        await fetch_all_pvs_async(
            data_model_version.data_model_key,
            data_model_version.external_version_number,
        )
    ).with_defaults(cde_keys)
    cache.set_pvs_batch(pv_catalog, expected_version=data_model_version)
    refined = refine_cde_types_from_pvs(cache.get_cde_catalog(), cache.get_all_pvs())
    cache.replace_cde_catalog(refined)
    pv_counts = {cde_key: len(values) for cde_key, values in pv_catalog.values.items()}
    total_pvs = sum(pv_counts.values())

    _router_logger.info(
        "Fetched PVs for session",
        extra={"file_id": file_id, "cde_count": len(pv_catalog), "pv_counts": pv_counts, "total_pvs": total_pvs},
    )

    # Warn if no PVs were found - likely indicates API issue or version mismatch
    if total_pvs == 0 and cde_keys:
        _router_logger.warning(
            "No PVs found for any CDE. PV combobox will not be available. "
            "Check Data Model Store API response and external_version_number.",
            extra={
                "file_id": file_id,
                "data_model_key": data_model_version.data_model_key,
                "external_version_number": data_model_version.external_version_number,
                "cde_keys": cde_keys,
            },
        )

    return pv_catalog


async def _fetch_pvs_for_session(
    file_id: str,
    cache: SessionCache,
    column_cde_map: ColumnCdeMap,
    data_model_version: DataModelVersionReference,
) -> CdePvCatalog:
    """Runs in parallel with harmonization to hide PV fetch latency."""
    cde_keys = column_cde_map.cde_keys()
    if cache.get_data_model_version() != data_model_version or not cache.has_cdes():
        raise RuntimeError("Reference-data cache does not match the workflow model version")
    existing = cache.get_all_pvs()
    missing_keys = [cde_key for cde_key in cde_keys if not existing.has(cde_key)]
    if not missing_keys:
        return existing
    return await _fetch_and_cache_pvs(cache, data_model_version, cde_keys, file_id)


def _read_manifest_if_exists(manifest_path: Path | None) -> ManifestSummary | None:
    if manifest_path is None or not manifest_path.exists():
        return None
    return read_manifest_parquet(manifest_path)


async def _store_and_adjust_manifest(
    file_id: str,
    manifest_path: Path,
    manifest_data: ManifestSummary,
    storage: UploadStorage,
    column_renames: ColumnRenameSet,
    column_pv_map: ColumnPvSets,
) -> ManifestSummary:
    """Must store before adjusting so later stages read the adjusted version."""
    stored_path = storage.save_harmonization_manifest(file_id, manifest_path)
    if stored_path is None:
        _router_logger.warning("Failed to store manifest", extra={"file_id": file_id})
        return manifest_data

    renamed_count = await _apply_column_renames_to_manifest(stored_path, column_renames)
    if renamed_count > 0:
        _router_logger.info("Applied column renames", extra={"file_id": file_id, "renamed_count": renamed_count})
        manifest_data = read_manifest_parquet(stored_path) or manifest_data

    adjustment_count = await _apply_pv_adjustments(stored_path, column_pv_map)
    if adjustment_count > 0:
        _router_logger.info("Applied PV adjustments", extra={"file_id": file_id, "adjustment_count": adjustment_count})
        return read_manifest_parquet(stored_path) or manifest_data

    return manifest_data


async def _read_store_and_adjust_manifest(
    file_id: str,
    manifest_path: Path | None,
    storage: UploadStorage,
    column_renames: ColumnRenameSet,
    column_pv_map: ColumnPvSets,
) -> HarmonizationManifestSummary | None:
    manifest_data = _read_manifest_if_exists(manifest_path)
    if manifest_data is None or manifest_path is None:
        return None

    final_data = await _store_and_adjust_manifest(
        file_id,
        manifest_path,
        manifest_data,
        storage,
        column_renames,
        column_pv_map,
    )
    return _convert_to_schema(final_data, column_pv_map)


async def _apply_column_renames_to_output(
    output_path: Path,
    column_renames: ColumnRenameSet,
    sheet_name: str | None,
) -> None:
    if not column_renames.renames or not output_path.exists():
        return

    dataset = await run_in_threadpool(read_tabular, output_path, sheet_name)
    renamed = apply_column_renames_to_dataset(dataset, column_renames)
    await run_in_threadpool(write_tabular, output_path, renamed, output_path)


async def _resolved_columns_for_source(
    source_path: Path,
    column_renames: ColumnRenameSet,
    sheet_name: str | None,
) -> tuple[ResolvedTabularColumn, ...]:
    if not source_path.exists():
        return ()
    dataset = await run_in_threadpool(read_tabular, source_path, sheet_name)
    return resolve_tabular_columns(dataset, column_renames)


async def _refresh_managed_harmonized_output(actual_path: Path, managed_path: Path) -> Path:
    if actual_path.resolve() == managed_path.resolve():
        return managed_path
    managed_path.parent.mkdir(parents=True, exist_ok=True)
    await run_in_threadpool(shutil.copy2, actual_path, managed_path)
    return managed_path


async def _apply_column_renames_to_manifest(manifest_path: Path, column_renames: ColumnRenameSet) -> int:
    return await run_in_threadpool(apply_column_renames_batch, manifest_path, column_renames)


def _compute_row_adjustment(
    row: ManifestRow, pv_set: frozenset[str]
) -> ManifestPvAdjustment | None:
    adjusted_value = compute_pv_adjustment(
        original_value=row.to_harmonize,
        top_harmonization=row.top_harmonization,
        top_suggestions=row.top_harmonizations,
        pv_set=pv_set,
    )
    if adjusted_value is None:
        return None
    return ManifestPvAdjustment.from_raw(
        row.column_key,
        row.to_harmonize,
        adjusted_value,
    )


def _process_row_for_adjustment(
    row: ManifestRow,
    column_pv_map: ColumnPvSets,
) -> ManifestPvAdjustment | None:
    """Skips columns without PVs — those don't need conformance adjustment."""
    pv_set = column_pv_map.get(row.column_key)
    if not pv_set:
        return None
    return _compute_row_adjustment(row, pv_set)


def _collect_pv_adjustments(
    rows: list[ManifestRow],
    column_pv_map: ColumnPvSets,
) -> list[ManifestPvAdjustment]:
    adjustments = [adj for row in rows if (adj := _process_row_for_adjustment(row, column_pv_map))]
    _log_non_conformant_samples(rows, column_pv_map)
    return adjustments


def _log_non_conformant_samples(rows: list[ManifestRow], column_pv_map: ColumnPvSets) -> None:
    """Capped at 5 samples from first 50 rows to avoid log spam while providing debugging signal."""
    samples = [
        {"column": row.column_name, "value": row.top_harmonization}
        # A small prefix sample is enough to diagnose bad PV coverage without
        # making large manifests expensive to log.
        for row in rows[:50]
        if _is_top_harmonization_non_conformant(row, column_pv_map)
    ][:5]
    if samples:
        _router_logger.warning(
            "Non-conformant values with no PV-compliant alternative",
            extra={"count": len(samples), "samples": samples},
        )


def _is_top_harmonization_non_conformant(row: ManifestRow, column_pv_map: ColumnPvSets) -> bool:
    """Logging-only check; the adjustment path in _compute_row_adjustment handles the actual fix."""
    pv_set = column_pv_map.get(row.column_key)
    return pv_set is not None and row.top_harmonization not in pv_set


async def _apply_pv_adjustments(manifest_path: Path, column_pv_map: ColumnPvSets) -> int:
    """AI harmonization may produce values outside the permissible value set; fix those."""
    if not any(pv_set for pv_set in column_pv_map.values.values()):
        return 0

    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        return 0

    adjustments = _collect_pv_adjustments(summary.rows, column_pv_map)
    if not adjustments:
        return 0

    return await run_in_threadpool(apply_pv_adjustments_batch, manifest_path, adjustments)


def _compute_column_stats(
    col_rows: list[ManifestRow],
    pv_set: frozenset[str] | None,
) -> ColumnStats:
    if not col_rows:
        return ColumnStats(0, 0, 0, 0, {bucket: 0 for bucket in ConfidenceBucket})

    finalized_outcomes = [_finalized_value_outcome(row, pv_set) for row in col_rows]
    summary = summarize_column_outcomes(finalized_outcomes)[0]
    confidence_counts: dict[ConfidenceBucket, int] = {b: 0 for b in ConfidenceBucket}
    for row, outcome in zip(col_rows, finalized_outcomes, strict=True):
        if outcome.is_changed:
            confidence_counts[confidence_bucket(row.confidence_score)] += 1

    return ColumnStats(
        summary.total_rows,
        summary.changed_rows,
        summary.changed_distinct_values,
        summary.non_conformant_distinct_values,
        confidence_counts,
    )


def _effective_ai_value(row: ManifestRow) -> str:
    """Treat a blank provider result as the manifest's pass-through sentinel."""
    if not row.top_harmonization.strip():
        return row.to_harmonize
    return row.top_harmonization


def _finalized_value_outcome(
    row: ManifestRow,
    pv_set: frozenset[str] | None,
) -> FinalizedValueOutcome:
    final_value = _effective_ai_value(row)
    return FinalizedValueOutcome(
        column_key=row.column_key,
        source_column_index=row.column_id,
        column_label=row.column_name,
        original_value=row.to_harmonize,
        final_value=final_value,
        final_value_source=(
            FinalValueSource.DATA_CHORD
            if final_value != row.to_harmonize
            else FinalValueSource.SOURCE
        ),
        occurrence_count=len(row.row_indices) if row.row_indices else 1,
        pv_set_available=bool(pv_set),
        is_pv_conformant=check_value_conformance(final_value, pv_set),
    )


def _create_breakdown_schema(
    outcome: ColumnOutcome,
    col_rows: list[ManifestRow],
    pv_set: frozenset[str] | None,
) -> HarmonizationColumnBreakdown:
    stats = _compute_column_stats(col_rows, pv_set)
    return HarmonizationColumnBreakdown(
        column_name=outcome.column_label,
        label=outcome.column_label or "Unknown",
        column_key=str(outcome.column_key),
        source_column_index=outcome.source_column_index,
        review_status=outcome.review_status,
        total_rows=outcome.total_rows,
        changed_rows=outcome.changed_rows,
        unchanged_rows=outcome.total_rows - outcome.changed_rows,
        unique_terms=outcome.total_distinct_values,
        unique_terms_changed=outcome.changed_distinct_values,
        unique_terms_unchanged=outcome.total_distinct_values - outcome.changed_distinct_values,
        non_conformant_terms=outcome.non_conformant_distinct_values,
        confidence_buckets_changed=[
            ConfidenceBucketCount(id=b.value, label=b.label, term_count=stats.confidence_counts[b])
            for b in ConfidenceBucket
        ],
    )


def _build_column_breakdowns(
    rows: list[ManifestRow],
    column_pv_map: ColumnPvSets,
) -> list[HarmonizationColumnBreakdown]:
    column_rows: dict[ColumnKey, list[ManifestRow]] = {}
    for row in rows:
        column_rows.setdefault(row.column_key, []).append(row)

    outcomes = summarize_column_outcomes([
        _finalized_value_outcome(row, column_pv_map.get(row.column_key))
        for row in rows
    ])
    return [
        _create_breakdown_schema(
            outcome,
            column_rows[outcome.column_key],
            column_pv_map.get(outcome.column_key),
        )
        for outcome in outcomes
    ]


def _convert_to_schema(
    manifest: ManifestSummary,
    column_pv_map: ColumnPvSets,
) -> HarmonizationManifestSummary:
    column_breakdowns = _build_column_breakdowns(manifest.rows, column_pv_map)
    total_non_conformant = sum(b.non_conformant_terms for b in column_breakdowns)
    return HarmonizationManifestSummary(
        total_terms=manifest.total_terms,
        changed_terms=manifest.changed_terms,
        high_confidence_count=manifest.high_confidence_count,
        medium_confidence_count=manifest.medium_confidence_count,
        low_confidence_count=manifest.low_confidence_count,
        non_conformant_terms=total_non_conformant,
        column_breakdowns=column_breakdowns,
    )


__all__ = ["stage_three_router"]
