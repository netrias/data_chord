"""
HTTP routes for triggering harmonization and building result summaries.

Fetches permissible values, then runs the configured harmonizer.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
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
from src.domain.harmonization import HarmonizeStatus
from src.domain.manifest import (
    ColumnMappingManifest,
)
from src.domain.reference_data import ReferenceDataError
from src.domain.tabular_column_renames import (
    ResolvedTabularColumn,
    apply_column_renames_to_dataset,
    resolve_tabular_columns,
)
from src.integrations.harmonize import HarmonizeResult
from src.persistence.cde_mapping_document_store import save_cde_mapping_document
from src.persistence.harmonization_job_store import HarmonizationJobState
from src.persistence.pv_manifest_store import ColumnPvSets, save_pv_snapshot
from src.persistence.review_override_store import delete_review_overrides_state
from src.persistence.workflow_artifacts import (
    load_upload_artifact,
    save_harmonized_artifacts,
)
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.shared.jinja import templates_for_stage
from src.stage_3_harmonize.manifest_processing import persist_and_summarize_manifest
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
from src.storage import UserContext, WorkflowFile, WorkflowNotFoundError, WorkflowStorage

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
    output_path = storage.harmonized_path_for(payload.file_id, meta.saved_path)

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
        output_path,
        meta.selected_sheet,
    )
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

    manifest_summary = await persist_and_summarize_manifest(
        payload.file_id,
        result.manifest_path,
        storage,
        column_renames,
        column_pv_sets,
        source_file_name=meta.original_name,
        reference_model_label=reference_model.label,
        reference_model_version=data_model_version.external_version_number,
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
            reference_model.catalog,
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


__all__ = ["stage_three_router"]
