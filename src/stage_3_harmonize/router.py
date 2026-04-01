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
    NO_MAPPING_SENTINEL,
    ColumnBreakdownSchema,
    ColumnMappingSet,
    ConfidenceBucketSchema,
    HarmonizeRequest,
    HarmonizeResponse,
    ManifestSummarySchema,
)
from src.domain.cde_mapping_persistence import save_cde_mapping
from src.domain.data_model_adapter import fetch_pvs_batch_async
from src.domain.data_model_cache import SessionCache, get_session_cache, populate_cde_cache
from src.domain.dependencies import (
    get_file_store,
    get_harmonize_service,
    get_upload_storage,
)
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
from src.domain.pv_persistence import save_pv_manifest_to_disk
from src.domain.pv_validation import check_value_conformance, compute_pv_adjustment
from src.domain.storage import FileType

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
    non_conformant_terms: int
    confidence_counts: dict[ConfidenceBucket, int]


@stage_three_router.get("", response_class=HTMLResponse, name="stage_three_entry")
async def render_stage_three(request: Request) -> HTMLResponse:
    context = {
        "request": request,
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

    store = get_file_store()
    store.delete(payload.file_id, FileType.REVIEW_OVERRIDES)

    stored_manifest = _storage.load_manifest(payload.file_id)
    manifest_payload = payload.manifest or cast(ManifestPayload | None, stored_manifest)
    column_mappings = ColumnMappingSet.from_dict(payload.manual_overrides)

    # Store column->CDE key mappings in cache for PV lookup
    cache = get_session_cache(payload.file_id)
    _store_column_mappings_in_cache(cache, manifest_payload, payload.manual_overrides)

    # Launch harmonization and PV fetch in parallel
    harmonize_task = asyncio.create_task(
        _run_harmonization(cache, meta.saved_path, payload.target_schema, column_mappings, manifest_payload)
    )
    pv_fetch_task = asyncio.create_task(
        _fetch_pvs_for_session(payload.file_id, manifest_payload, payload.manual_overrides, payload.target_schema)
    )

    # Wait for both to complete
    result, _ = await asyncio.gather(harmonize_task, pv_fetch_task)

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

    # Relocate harmonized CSV from CWD into managed storage
    _storage.relocate_harmonized_output(payload.file_id, meta.saved_path)

    # Store manifest and apply PV adjustments
    manifest_summary = await _read_store_and_adjust_manifest(
        payload.file_id, result.manifest_path
    )
    _router_logger.info(
        "Manifest summary result",
        extra={"file_id": payload.file_id, "has_summary": manifest_summary is not None},
    )

    # Persist CDE mapping decisions from Stage 2 for inclusion in the download zip
    if payload.mapping_decisions:
        _, version_label = cache.get_model_info()
        save_cde_mapping(payload.file_id, payload.mapping_decisions, payload.target_schema, version_label or None)

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
    """targetField may be absent or non-string in externally produced manifests."""
    if not isinstance(entry, dict):
        return None
    target = entry.get("targetField")
    return target if isinstance(target, str) else None


def _extract_column_cde_mappings(manifest: ManifestPayload | None) -> dict[str, str]:
    """PV validation and cache storage both need column→CDE key lookups."""
    if manifest is None:
        return {}
    column_mappings = manifest.get("column_mappings", {})
    return {col: target for col, entry in column_mappings.items() if (target := _get_target_field(entry))}


def _effective_column_cde_map(
    manifest: ManifestPayload | None, manual_overrides: dict[str, str]
) -> dict[str, str]:
    """Merge AI mappings with user overrides, filtering out explicit "No Mapping" selections."""
    mappings = _extract_column_cde_mappings(manifest)
    mappings.update(manual_overrides)
    return {k: v for k, v in mappings.items() if v != NO_MAPPING_SENTINEL}


def _store_column_mappings_in_cache(
    cache: SessionCache, manifest: ManifestPayload | None, manual_overrides: dict[str, str]
) -> None:
    """PV validation needs to know which CDE each column maps to."""
    mappings = _effective_column_cde_map(manifest, manual_overrides)
    cache.set_column_mappings(mappings)
    _router_logger.info("Stored column→CDE mappings", extra={"mappings": mappings})


async def _run_harmonization(
    cache: SessionCache,
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
        cache=cache,
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
    _router_logger.info(
        "Fetching PVs from Data Model Store",
        extra={
            "file_id": file_id,
            "data_model_key": data_model_key,
            "version_label": version_label,
            "cde_keys": cde_keys,
        },
    )
    pv_map = await fetch_pvs_batch_async(data_model_key, version_label, cde_keys)
    cache.set_pvs_batch(pv_map)
    pv_counts = {k: len(v) for k, v in pv_map.items()}
    total_pvs = sum(pv_counts.values())

    _router_logger.info(
        "Fetched PVs for session",
        extra={"file_id": file_id, "cde_count": len(pv_map), "pv_counts": pv_counts, "total_pvs": total_pvs},
    )

    # Warn if no PVs were found - likely indicates API issue or version mismatch
    if total_pvs == 0 and cde_keys:
        _router_logger.warning(
            "No PVs found for any CDE. PV combobox will not be available. "
            "Check Data Model Store API response and version_label.",
            extra={
                "file_id": file_id,
                "data_model_key": data_model_key,
                "version_label": version_label,
                "cde_keys": cde_keys,
            },
        )

    # Persist PV manifest to disk for recovery after server restart
    save_pv_manifest_to_disk(file_id, cache, pv_map)


async def _fetch_pvs_for_session(
    file_id: str, manifest: ManifestPayload | None, manual_overrides: dict[str, str], target_schema: str
) -> None:
    """Runs in parallel with harmonization to hide PV fetch latency."""
    cache = get_session_cache(file_id)
    column_cde_map = _effective_column_cde_map(manifest, manual_overrides)
    cde_keys = list(set(column_cde_map.values()))

    # Server restart between Stage 2 and Stage 3 clears in-memory CDEs; re-fetch.
    if not cache.has_cdes():
        _router_logger.info("CDEs missing from cache; re-fetching from Data Model Store", extra={"file_id": file_id})
        await run_in_threadpool(populate_cde_cache, file_id, target_schema)

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
    cache = get_session_cache(file_id)
    column_pv_map = {
        name: cache.get_pvs_for_column(name)
        for name in {r.column_name for r in final_data.rows}
    }
    return _convert_to_schema(final_data, column_pv_map)


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
    """Skips columns without PVs — those don't need conformance adjustment."""
    pv_set = cache.get_pvs_for_column(row.column_name)
    if not pv_set:
        return None
    return _compute_row_adjustment(row, pv_set)


def _collect_pv_adjustments(rows: list[ManifestRow], cache: SessionCache) -> list[PVAdjustmentRecord]:
    adjustments = [adj for row in rows if (adj := _process_row_for_adjustment(row, cache))]
    _log_non_conformant_samples(rows, cache)
    return adjustments


def _log_non_conformant_samples(rows: list[ManifestRow], cache: SessionCache) -> None:
    """Capped at 5 samples from first 50 rows to avoid log spam while providing debugging signal."""
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
    """Logging-only check; the adjustment path in _compute_row_adjustment handles the actual fix."""
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


def _compute_column_stats(
    col_rows: list[ManifestRow],
    pv_set: frozenset[str] | None,
) -> ColumnStats:
    total_rows = 0
    changed_rows = 0
    unique_terms_changed = 0
    non_conformant_terms = 0
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

        if not check_value_conformance(row.top_harmonization, pv_set):
            non_conformant_terms += 1

    return ColumnStats(total_rows, changed_rows, unique_terms_changed, non_conformant_terms, confidence_counts)


def _create_breakdown_schema(
    column_name: str,
    col_rows: list[ManifestRow],
    pv_set: frozenset[str] | None,
) -> ColumnBreakdownSchema:
    stats = _compute_column_stats(col_rows, pv_set)
    unique_terms = len(col_rows)
    return ColumnBreakdownSchema(
        column_name=column_name,
        label=column_name or "Unknown",
        total_rows=stats.total_rows,
        changed_rows=stats.changed_rows,
        unchanged_rows=stats.total_rows - stats.changed_rows,
        unique_terms=unique_terms,
        unique_terms_changed=stats.unique_terms_changed,
        unique_terms_unchanged=unique_terms - stats.unique_terms_changed,
        non_conformant_terms=stats.non_conformant_terms,
        confidence_buckets_changed=[
            ConfidenceBucketSchema(id=b.value, label=b.label, term_count=stats.confidence_counts[b])
            for b in ConfidenceBucket
        ],
    )


def _build_column_breakdowns(
    rows: list[ManifestRow],
    column_pv_map: dict[str, frozenset[str] | None],
) -> list[ColumnBreakdownSchema]:
    column_rows: dict[str, list[ManifestRow]] = defaultdict(list)
    for row in rows:
        column_rows[row.column_name].append(row)

    breakdowns = [
        _create_breakdown_schema(name, col_rows, column_pv_map.get(name))
        for name, col_rows in column_rows.items()
    ]
    # Columns needing attention (changes OR non-conformant) sort first
    breakdowns.sort(key=lambda b: (b.changed_rows == 0 and b.non_conformant_terms == 0, -b.total_rows))
    return breakdowns


def _convert_to_schema(
    manifest: ManifestSummary,
    column_pv_map: dict[str, frozenset[str] | None],
) -> ManifestSummarySchema:
    column_breakdowns = _build_column_breakdowns(manifest.rows, column_pv_map)
    total_non_conformant = sum(b.non_conformant_terms for b in column_breakdowns)
    return ManifestSummarySchema(
        total_terms=manifest.total_terms,
        changed_terms=manifest.changed_terms,
        high_confidence_count=manifest.high_confidence_count,
        medium_confidence_count=manifest.medium_confidence_count,
        low_confidence_count=manifest.low_confidence_count,
        non_conformant_terms=total_non_conformant,
        column_breakdowns=column_breakdowns,
    )


__all__ = ["stage_three_router"]
