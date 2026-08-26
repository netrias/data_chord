"""
HTTP routes for triggering harmonization and building result summaries.

Fetches permissible values, then runs the configured harmonizer.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from netrias_client import read_tabular, write_tabular

import src.app.dependencies as dependencies
from src.api.schemas import (
    HarmonizeRequest,
    HarmonizeResponse,
)
from src.app.dependencies import (
    get_harmonize_service,
)
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_renames import ColumnRenameSet
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import (
    HarmonizationManifestSummary,
    HarmonizeStatus,
)
from src.domain.manifest import (
    ColumnMappingManifest,
    ManifestPvAdjustment,
    ManifestRow,
    ManifestSummary,
)
from src.domain.pv_validation import compute_pv_adjustment
from src.domain.reference_data import ReferenceDataError
from src.domain.tabular_column_renames import (
    ResolvedTabularColumn,
    apply_column_renames_to_dataset,
    resolve_tabular_columns,
)
from src.integrations.harmonize import HarmonizeResult
from src.persistence.cde_mapping_document_store import save_cde_mapping_document
from src.persistence.harmonization_job_store import HarmonizationJobState
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_writer import apply_column_renames_batch, apply_pv_adjustments_batch
from src.persistence.pv_manifest_store import ColumnPvSets, save_pv_snapshot_if_unchanged
from src.persistence.review_override_store import delete_review_overrides_state
from src.persistence.workflow_artifacts import (
    load_upload_artifact,
    save_harmonized_artifacts,
)
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.shared.jinja import templates_for_stage
from src.stage_3_harmonize.result_summary import build_harmonization_manifest_summary
from src.stage_3_harmonize.use_cases import (
    HarmonizationStart,
    HarmonizationStartConflictError,
    HarmonizationWorkflowNotFoundError,
    HarmonizationWorkflowUnreadableError,
    RunAuthority,
    StaleStageThreeWorkerError,
    capture_harmonization_artifact_versions,
    complete_stage_three_job,
    fail_stage_three_job,
    heartbeat_stage_three_job,
    load_authorized_job,
    start_harmonization,
)
from src.storage import (
    UserContext,
    VersionToken,
    WorkflowConflictError,
    WorkflowFile,
    WorkflowStorage,
)

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
NEXT_STAGE_PATH = "/stage-4"
JOB_START_GRACE_SECONDS = 0.25

_templates = templates_for_stage(TEMPLATE_DIR)
_router_logger = logging.getLogger(__name__)

stage_three_router = APIRouter(prefix="/stage-3", tags=["Stage 3 Harmonize"])


_stage_three_tasks: dict[str, asyncio.Task[None]] = {}


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
        _cleanup_worker_output(payload.file_id, accepted_job.worker_id)
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
    artifact_versions = capture_harmonization_artifact_versions(
        workflow_storage,
        user,
        payload.file_id,
    )

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
        workflow_state.selected_sheet,
    )

    try:
        reference_model = await run_in_threadpool(
            dependencies.get_reference_data_repository().load_model,
            data_model_version,
        )
    except ReferenceDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reference data is currently unavailable. Please try again later.",
        ) from exc
    prepared_manifest = manifest.apply_choices(column_overrides, column_renames, reference_model.catalog)
    column_cde_map = prepared_manifest.column_cde_map()
    managed_output_path = storage.harmonized_path_for(payload.file_id, meta.saved_path)
    worker_output_path = _worker_scratch_path(managed_output_path, run_authority.worker_id)

    selected_cde_keys = column_cde_map.cde_keys()
    pv_catalog = CdePvCatalog.from_mapping({
        cde_key: reference_model.pvs.values[cde_key]
        for cde_key in selected_cde_keys
    })
    column_pv_sets = ColumnPvSets({
        column_key: pv_catalog.get(cde_key)
        for column_key, cde_key in column_cde_map.mappings.items()
    })
    result = await _run_harmonization(
        meta.saved_path,
        data_model_version,
        prepared_manifest,
        column_pv_sets,
        worker_output_path,
        workflow_state.selected_sheet,
    )
    if result.status == HarmonizeStatus.SUCCEEDED:
        # Provider output is still private scratch at this point. Refuse
        # to transform it if this worker or its plan was superseded.
        run_authority.require_current()
        run_authority.require_plan_current()
    harmonized_output_path = result.output_path or worker_output_path
    if result.status == HarmonizeStatus.SUCCEEDED:
        await _apply_column_renames_to_output(
            harmonized_output_path,
            column_renames,
            workflow_state.selected_sheet,
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

    manifest_summary = await _read_and_adjust_manifest(
        result.manifest_path,
        column_renames,
        column_pv_sets,
        source_file_name=meta.original_name,
        reference_model_label=reference_model.label,
        reference_model_version=data_model_version.external_version_number,
    )
    if result.status == HarmonizeStatus.SUCCEEDED:
        if not harmonized_output_path.exists() or result.manifest_path is None or manifest_summary is None:
            raise RuntimeError("Harmonization completed without required output artifacts")
        run_authority.require_current()
        run_authority.require_plan_current()
        await run_in_threadpool(
            save_pv_snapshot_if_unchanged,
            workflow_storage,
            user,
            loaded_state,
            pv_catalog,
            expected_version=artifact_versions.pv_manifest,
        )
        run_authority.require_current()
        run_authority.require_plan_current()
        await run_in_threadpool(
            save_cde_mapping_document,
            workflow_storage,
            user,
            payload.file_id,
            manifest,
            column_overrides,
            column_renames,
            resolved_columns,
            reference_model.catalog,
            data_model_version,
            expected_version=artifact_versions.cde_mapping,
        )
        run_authority.require_current()
        run_authority.require_plan_current()
        await run_in_threadpool(
            save_harmonized_artifacts,
            workflow_storage,
            user,
            payload.file_id,
            harmonized_output_path,
            result.manifest_path,
            expected_harmonized_output_version=artifact_versions.harmonized_output,
            expected_manifest_version=artifact_versions.manifest,
        )
        await run_in_threadpool(
            storage.save_harmonization_manifest,
            payload.file_id,
            result.manifest_path,
        )
        await run_in_threadpool(
            storage.restore_harmonized_output,
            payload.file_id,
            meta.saved_path,
            harmonized_output_path,
        )
        run_authority.require_current()
        run_authority.require_plan_current()
        await run_in_threadpool(
            _invalidate_previous_stage_three_review,
            workflow_storage,
            user,
            payload.file_id,
            artifact_versions.review_overrides,
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
    column_pv_sets: ColumnPvSets,
    output_path: Path,
    sheet_name: str | None,
) -> HarmonizeResult:
    """The provider is synchronous; keep its work off the event loop."""
    harmonizer = get_harmonize_service()
    return await run_in_threadpool(
        harmonizer.run,
        file_path=file_path,
        data_model_version=data_model_version,
        prepared_manifest=prepared_manifest,
        column_pv_sets=column_pv_sets,
        output_path=output_path,
        sheet_name=sheet_name,
    )


def _read_manifest_if_exists(manifest_path: Path | None) -> ManifestSummary | None:
    if manifest_path is None or not manifest_path.exists():
        return None
    return read_manifest_parquet(manifest_path)


async def _adjust_manifest(
    manifest_path: Path,
    manifest_data: ManifestSummary,
    column_renames: ColumnRenameSet,
    column_pv_map: ColumnPvSets,
) -> ManifestSummary:
    """Adjust one worker's private manifest before durable publication."""
    renamed_count = await _apply_column_renames_to_manifest(manifest_path, column_renames)
    if renamed_count > 0:
        _router_logger.info("Applied column renames", extra={"renamed_count": renamed_count})
        manifest_data = read_manifest_parquet(manifest_path) or manifest_data

    adjustment_count = await _apply_pv_adjustments(manifest_path, column_pv_map)
    if adjustment_count > 0:
        _router_logger.info("Applied PV adjustments", extra={"adjustment_count": adjustment_count})
        return read_manifest_parquet(manifest_path) or manifest_data

    return manifest_data


async def _read_and_adjust_manifest(
    manifest_path: Path | None,
    column_renames: ColumnRenameSet,
    column_pv_map: ColumnPvSets,
    *,
    source_file_name: str,
    reference_model_label: str,
    reference_model_version: str,
) -> HarmonizationManifestSummary | None:
    manifest_data = _read_manifest_if_exists(manifest_path)
    if manifest_data is None or manifest_path is None:
        return None

    final_data = await _adjust_manifest(
        manifest_path,
        manifest_data,
        column_renames,
        column_pv_map,
    )
    return build_harmonization_manifest_summary(
        final_data,
        column_pv_map,
        source_file_name=source_file_name,
        reference_model_label=reference_model_label,
        reference_model_version=reference_model_version,
    )


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


def _worker_scratch_path(managed_path: Path, worker_id: str) -> Path:
    """Keep concurrent provider runs from sharing one mutable output path."""
    return managed_path.with_stem(f"{managed_path.stem}.{worker_id}")


def _cleanup_worker_output(file_id: str, worker_id: str) -> None:
    storage = dependencies.get_upload_storage()
    meta = storage.load(file_id)
    if meta is None:
        return
    managed_path = storage.harmonized_path_for(file_id, meta.saved_path)
    _worker_scratch_path(managed_path, worker_id).unlink(missing_ok=True)


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
    """Log bounded structural evidence without source headers or data values."""
    samples = [
        row
        # A small prefix sample is enough to diagnose bad PV coverage without
        # making large manifests expensive to log.
        for row in rows[:50]
        if _is_top_harmonization_non_conformant(row, column_pv_map)
    ][:5]
    if samples:
        _router_logger.warning(
            "Non-conformant values with no PV-compliant alternative",
            extra={
                "sample_count": len(samples),
                "sample_column_keys": sorted({str(row.column_key) for row in samples}),
                "scanned_row_count": min(len(rows), 50),
            },
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


def _invalidate_previous_stage_three_review(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    expected_version: VersionToken | None,
) -> None:
    """Delete only review state unchanged since this worker captured it."""
    if expected_version is not None:
        try:
            delete_review_overrides_state(
                workflow_storage,
                user,
                file_id,
                expected_version=expected_version,
            )
        except WorkflowConflictError:
            _router_logger.info(
                "Preserving review overrides changed during harmonization",
                extra={"file_id": file_id},
            )
            raise StaleStageThreeWorkerError(file_id) from None
        if workflow_storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES) is not None:
            raise StaleStageThreeWorkerError(file_id)
    elif workflow_storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES) is not None:
        raise StaleStageThreeWorkerError(file_id)


__all__ = ["stage_three_router"]
