"""
HTTP routes for triggering harmonization and building result summaries.

Orchestrates parallel harmonization and PV fetch tasks.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple, cast
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.domain import (
    ColumnBreakdownSchema,
    ColumnMappingSet,
    ConfidenceBucketSchema,
    HarmonizeRequest,
    HarmonizeResponse,
    ManifestSummarySchema,
    format_column_label,
    get_default_target_schema,
)
from src.domain.data_model_cache import SessionCache, get_session_cache
from src.domain.dependencies import get_data_model_client, get_harmonize_service, get_upload_storage
from src.domain.harmonize import HarmonizeResult
from src.domain.manifest import (
    ConfidenceBucket,
    ManifestPayload,
    ManifestRow,
    ManifestSummary,
    confidence_bucket,
    is_value_changed,
    read_manifest_parquet,
)
from src.domain.manifest.writer import apply_pv_adjustments_batch
from src.domain.pv_validation import compute_pv_adjustment

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
NEXT_STAGE_PATH = "/stage-4"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
_storage = get_upload_storage()
_router_logger = logging.getLogger(__name__)

stage_three_router = APIRouter(prefix="/stage-3", tags=["Stage 3 Harmonize"])


class ModelInfo(NamedTuple):
    data_model_key: str
    version_label: str


class PVAdjustmentRecord(NamedTuple):
    column_name: str
    to_harmonize: str
    adjusted_value: str
    source: str


class ColumnStats(NamedTuple):
    total_rows: int
    changed_rows: int
    unique_terms_changed: int
    confidence_counts: dict[ConfidenceBucket, int]


@stage_three_router.get("", response_class=HTMLResponse, name="stage_three_entry")
async def render_stage_three(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "default_schema": get_default_target_schema(),
        "next_stage_url": NEXT_STAGE_PATH,
    }
    return _templates.TemplateResponse("stage_3_harmonize.html", context)


@stage_three_router.post(
    "/harmonize",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize",
)
async def harmonize_dataset(payload: HarmonizeRequest) -> HarmonizeResponse:
    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please rerun analysis.")

    stored_manifest = _storage.load_manifest(payload.file_id)
    manifest_payload = payload.manifest or cast(ManifestPayload | None, stored_manifest)
    column_mappings = ColumnMappingSet.from_dict(payload.manual_overrides)

    # Store column->CDE key mappings in cache for PV lookup
    cache = get_session_cache(payload.file_id)
    _store_column_mappings_in_cache(cache, manifest_payload)

    # Launch harmonization and PV fetch in parallel
    harmonize_task = asyncio.create_task(
        _run_harmonization(meta.saved_path, payload.target_schema, column_mappings, manifest_payload)
    )
    pv_fetch_task = asyncio.create_task(
        _fetch_pvs_for_session(payload.file_id, manifest_payload)
    )

    # Wait for both to complete
    result, _ = await asyncio.gather(harmonize_task, pv_fetch_task)

    _router_logger.info(
        "Harmonization job dispatched",
        extra={"file_id": payload.file_id, "job_id": result.job_id, "status": result.status},
    )

    # Store manifest and apply PV adjustments
    manifest_summary = await _read_store_and_adjust_manifest(
        payload.file_id, result.manifest_path
    )

    query_params = urlencode({
        "file_id": payload.file_id,
        "job_id": result.job_id,
        "status": result.status,
        "detail": result.detail or "",
    })
    next_stage_url = f"{NEXT_STAGE_PATH}?{query_params}"
    return HarmonizeResponse(
        job_id=result.job_id,
        status=result.status,
        detail=result.detail,
        next_stage_url=next_stage_url,
        job_id_available=result.job_id_available,
        manifest_summary=manifest_summary,
    )


def _get_target_field(entry: object) -> str | None:
    """Extract targetField from a column mapping entry if valid."""
    if not isinstance(entry, dict):
        return None
    target = entry.get("targetField")
    return target if isinstance(target, str) else None


def _extract_column_cde_mappings(manifest: ManifestPayload | None) -> dict[str, str]:
    """Extract column→CDE key mappings from manifest's column_mappings."""
    if manifest is None:
        return {}
    column_mappings = manifest.get("column_mappings", {})
    return {col: target for col, entry in column_mappings.items() if (target := _get_target_field(entry))}


def _store_column_mappings_in_cache(cache: SessionCache, manifest: ManifestPayload | None) -> None:
    """PV validation needs to know which CDE each column maps to."""
    mappings = _extract_column_cde_mappings(manifest)
    cache.set_column_mappings(mappings)
    _router_logger.info("Stored column→CDE mappings", extra={"mappings": mappings})


async def _run_harmonization(
    file_path: Path,
    target_schema: str,
    column_mappings: ColumnMappingSet,
    manifest: ManifestPayload | None,
) -> HarmonizeResult:
    """Netrias client is sync; run in threadpool to avoid blocking the event loop."""
    harmonizer = get_harmonize_service()
    return await run_in_threadpool(
        harmonizer.run,
        file_path=file_path,
        target_schema=target_schema,
        column_mappings=column_mappings,
        manifest=manifest,
    )


def _validate_pv_fetch_preconditions(
    cache: SessionCache, cde_keys: list[str], file_id: str
) -> ModelInfo | None:
    """Early-exit checks consolidated here to keep the main fetch function simple."""
    if not cache.has_cdes():
        _router_logger.warning("No CDEs in cache for PV fetch", extra={"file_id": file_id})
        return None
    if not cde_keys:
        return None
    data_model_key, version_label = cache.get_model_info()
    if not data_model_key or not version_label:
        _router_logger.warning("Missing model info for PV fetch", extra={"file_id": file_id})
        return None
    return ModelInfo(data_model_key, version_label)


async def _fetch_and_cache_pvs(
    cache: SessionCache, data_model_key: str, version_label: str, cde_keys: list[str], file_id: str
) -> None:
    client = get_data_model_client()
    pv_map = await run_in_threadpool(client.fetch_pvs_batch, data_model_key, version_label, cde_keys)
    cache.set_pvs_batch(pv_map)
    pv_counts = {k: len(v) for k, v in pv_map.items()}
    _router_logger.info(
        "Fetched PVs for session",
        extra={"file_id": file_id, "cde_count": len(pv_map), "pv_counts": pv_counts},
    )


async def _fetch_pvs_for_session(file_id: str, manifest: ManifestPayload | None) -> None:
    """Runs in parallel with harmonization to hide PV fetch latency."""
    cache = get_session_cache(file_id)
    column_cde_map = _extract_column_cde_mappings(manifest)
    cde_keys = list(set(column_cde_map.values()))

    model_info = _validate_pv_fetch_preconditions(cache, cde_keys, file_id)
    if model_info is None:
        return

    try:
        await _fetch_and_cache_pvs(cache, model_info.data_model_key, model_info.version_label, cde_keys, file_id)
    except Exception:
        _router_logger.exception("Failed to fetch PVs for session", extra={"file_id": file_id})


def _read_manifest_if_exists(manifest_path: Path | None) -> ManifestSummary | None:
    if manifest_path is None or not manifest_path.exists():
        return None
    return read_manifest_parquet(manifest_path)


async def _store_and_adjust_manifest(
    file_id: str, manifest_path: Path, manifest_data: ManifestSummary
) -> ManifestSummary:
    """Must store before adjusting so later stages read the adjusted version."""
    stored_path = _storage.save_harmonization_manifest(file_id, manifest_path)
    if stored_path is None:
        _router_logger.warning("Failed to store manifest", extra={"file_id": file_id})
        return manifest_data

    adjustment_count = await _apply_pv_adjustments(file_id, stored_path)
    if adjustment_count > 0:
        _router_logger.info("Applied PV adjustments", extra={"file_id": file_id, "adjustment_count": adjustment_count})
        return read_manifest_parquet(stored_path) or manifest_data

    return manifest_data


async def _read_store_and_adjust_manifest(
    file_id: str, manifest_path: Path | None
) -> ManifestSummarySchema | None:
    manifest_data = _read_manifest_if_exists(manifest_path)
    if manifest_data is None or manifest_path is None:
        return None

    final_data = await _store_and_adjust_manifest(file_id, manifest_path, manifest_data)
    return _convert_to_schema(final_data)


def _compute_row_adjustment(
    row: ManifestRow, pv_set: frozenset[str]
) -> PVAdjustmentRecord | None:
    result = compute_pv_adjustment(
        original_value=row.to_harmonize,
        top_harmonization=row.top_harmonization,
        top_suggestions=row.top_harmonizations,
        pv_set=pv_set,
    )
    if result.adjusted_value is None or result.adjustment_source is None:
        return None
    if result.adjusted_value == row.top_harmonization:
        return None
    return PVAdjustmentRecord(row.column_name, row.to_harmonize, result.adjusted_value, result.adjustment_source.value)


def _process_row_for_adjustment(
    row: ManifestRow, cache: SessionCache
) -> PVAdjustmentRecord | None:
    """Check if row needs PV adjustment; returns adjustment or None."""
    pv_set = cache.get_pvs_for_column(row.column_name)
    if not pv_set:
        return None
    return _compute_row_adjustment(row, pv_set)


def _collect_pv_adjustments(rows: list[ManifestRow], cache: SessionCache) -> list[PVAdjustmentRecord]:
    adjustments = [adj for row in rows if (adj := _process_row_for_adjustment(row, cache))]
    _log_non_conformant_samples(rows, cache)
    return adjustments


def _log_non_conformant_samples(rows: list[ManifestRow], cache: SessionCache) -> None:
    """Log samples of non-conformant values that couldn't be auto-adjusted."""
    samples = [
        {"column": row.column_name, "value": row.top_harmonization}
        for row in rows[:50]  # Limit scan to first 50 rows
        if _is_top_harmonization_non_conformant(row, cache)
    ][:5]  # Keep only first 5 samples
    if samples:
        _router_logger.warning(
            "Non-conformant values with no PV-compliant alternative",
            extra={"count": len(samples), "samples": samples},
        )


def _is_top_harmonization_non_conformant(row: ManifestRow, cache: SessionCache) -> bool:
    """Check if row's top harmonization is outside PV set (for logging only)."""
    pv_set = cache.get_pvs_for_column(row.column_name)
    return pv_set is not None and row.top_harmonization not in pv_set


def _records_to_tuples(records: list[PVAdjustmentRecord]) -> list[tuple[str, str, str, str]]:
    """Writer API expects plain tuples; convert from typed records."""
    return [(r.column_name, r.to_harmonize, r.adjusted_value, r.source) for r in records]


async def _apply_pv_adjustments(file_id: str, manifest_path: Path) -> int:
    """AI harmonization may produce values outside the permissible value set; fix those."""
    cache = get_session_cache(file_id)
    if not cache.has_any_pvs():
        return 0

    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        return 0

    adjustments = _collect_pv_adjustments(summary.rows, cache)
    if not adjustments:
        return 0

    return await run_in_threadpool(apply_pv_adjustments_batch, manifest_path, _records_to_tuples(adjustments))


def _compute_column_stats(col_rows: list[ManifestRow]) -> ColumnStats:
    total_rows = 0
    changed_rows = 0
    unique_terms_changed = 0
    confidence_counts: dict[ConfidenceBucket, int] = {b: 0 for b in ConfidenceBucket}

    for row in col_rows:
        # Each ManifestRow is one unique term; row_indices lists source rows with that term.
        # Empty list means row tracking unavailable (treat as 1 occurrence).
        row_count = len(row.row_indices) if row.row_indices else 1
        total_rows += row_count
        if is_value_changed(row.to_harmonize, row.top_harmonization):
            changed_rows += row_count
            unique_terms_changed += 1
            confidence_counts[confidence_bucket(row.confidence_score)] += 1

    return ColumnStats(total_rows, changed_rows, unique_terms_changed, confidence_counts)


def _create_breakdown_schema(column_name: str, col_rows: list[ManifestRow]) -> ColumnBreakdownSchema:
    stats = _compute_column_stats(col_rows)
    unique_terms = len(col_rows)
    return ColumnBreakdownSchema(
        column_name=column_name,
        label=format_column_label(column_name),
        total_rows=stats.total_rows,
        changed_rows=stats.changed_rows,
        unchanged_rows=stats.total_rows - stats.changed_rows,
        unique_terms=unique_terms,
        unique_terms_changed=stats.unique_terms_changed,
        unique_terms_unchanged=unique_terms - stats.unique_terms_changed,
        confidence_buckets_changed=[
            ConfidenceBucketSchema(id=b.value, label=b.label, term_count=stats.confidence_counts[b])
            for b in ConfidenceBucket
        ],
    )


def _build_column_breakdowns(rows: list[ManifestRow]) -> list[ColumnBreakdownSchema]:
    column_rows: dict[str, list[ManifestRow]] = defaultdict(list)
    for row in rows:
        column_rows[row.column_name].append(row)

    breakdowns = [_create_breakdown_schema(name, col_rows) for name, col_rows in column_rows.items()]
    breakdowns.sort(key=lambda b: (b.changed_rows == 0, -b.total_rows))
    return breakdowns


def _convert_to_schema(manifest: ManifestSummary) -> ManifestSummarySchema:
    column_breakdowns = _build_column_breakdowns(manifest.rows)
    return ManifestSummarySchema(
        total_terms=manifest.total_terms,
        changed_terms=manifest.changed_terms,
        high_confidence_count=manifest.high_confidence_count,
        medium_confidence_count=manifest.medium_confidence_count,
        low_confidence_count=manifest.low_confidence_count,
        column_breakdowns=column_breakdowns,
    )


__all__ = ["stage_three_router"]
