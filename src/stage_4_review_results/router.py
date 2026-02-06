"""
HTTP routes for reviewing harmonized results and applying manual overrides.

Manages review state and coordinates manifest updates with PV validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.domain import CONFIDENCE, RecommendationType, format_column_label
from src.domain.data_model_cache import SessionCache, get_session_cache
from src.domain.dependencies import get_file_store, get_upload_storage
from src.domain.manifest import (
    ConfidenceBucket,
    ManifestRow,
    ManifestSummary,
    add_manual_overrides_batch,
    confidence_bucket,
    get_latest_override_value,
    is_value_changed,
    read_manifest_parquet,
)
from src.domain.pv_persistence import ensure_pvs_loaded
from src.domain.pv_validation import check_value_conformance
from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN
from src.domain.storage import FileType, UploadStorage, load_csv
from src.stage_4_review_results.schemas import (
    ColumnReviewData,
    DeleteOverridesResponse,
    NonConformantItem,
    NonConformantResponse,
    ReviewOverridesSchema,
    ReviewStateSchema,
    RowContextRequest,
    RowContextResponse,
    SaveOverridesRequest,
    SaveOverridesResponse,
    StageFourResultsResponse,
    SuggestionInfo,
    Transformation,
)

logger = logging.getLogger(__name__)

_MODULE_DIR = FilePath(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"


_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


class StageFourResultsRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)
    manual_columns: list[str] = []


stage_four_router = APIRouter(prefix="/stage-4", tags=["Stage 4 Review"])


@stage_four_router.get("", response_class=HTMLResponse, name="stage_four_review_page")
async def render_stage_four(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "results_endpoint": request.url_for("stage_four_harmonized_rows"),
    }
    return _templates.TemplateResponse("stage_4_review.html", context)


@stage_four_router.post("/rows", response_model=StageFourResultsResponse, name="stage_four_harmonized_rows")
async def fetch_stage_four_rows(payload: StageFourResultsRequest) -> StageFourResultsResponse:
    import time
    t0 = time.perf_counter()

    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.")

    t1 = time.perf_counter()
    _, original_rows = load_csv(meta.saved_path)
    t2 = time.perf_counter()
    logger.info(f"[PERF] load_csv: {t2 - t1:.3f}s")

    manifest = _load_manifest(storage, payload.file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Harmonization manifest not found. Please rerun Stage 3.")
    t3 = time.perf_counter()
    logger.info(f"[PERF] load_manifest: {t3 - t2:.3f}s")

    column_info = _extract_columns_from_manifest(manifest)
    t4 = time.perf_counter()

    # Load PVs before building transformations so pvSetAvailable/isPVConformant are populated
    column_pvs = _build_column_pvs(column_info, payload.file_id)
    t5 = time.perf_counter()
    logger.info(f"[PERF] build_column_pvs: {t5 - t4:.3f}s")

    columns = _build_columns_from_manifest(manifest, payload.file_id)
    t6 = time.perf_counter()
    logger.info(f"[PERF] build_columns_from_manifest: {t6 - t5:.3f}s, columns={len(columns)}")
    logger.info(f"[PERF] total: {t6 - t0:.3f}s")

    return StageFourResultsResponse(
        columns=columns,
        columnPVs=column_pvs,
        totalOriginalRows=len(original_rows),
    )


@dataclass
class _ColumnInfo:
    column_name: str
    label: str
    source_index: int  # Position in original spreadsheet for ordering


def _extract_columns_from_manifest(manifest: ManifestSummary) -> list[_ColumnInfo]:
    """Extract unique columns with their source index for ordering."""
    seen: set[str] = set()
    columns: list[_ColumnInfo] = []
    for row in manifest.rows:
        col_name = row.column_name
        if col_name and col_name not in seen:
            seen.add(col_name)
            columns.append(_ColumnInfo(
                column_name=col_name,
                label=format_column_label(col_name),
                source_index=row.column_id,
            ))
    return columns


def _build_columns_from_manifest(manifest: ManifestSummary, file_id: str) -> list[ColumnReviewData]:
    """Build column-centric data structure grouped by column name."""
    cache = get_session_cache(file_id)

    # Group manifest rows by column, track source index for ordering
    columns_map: dict[str, list[ManifestRow]] = {}
    column_indices: dict[str, int] = {}

    for row in manifest.rows:
        col_name = row.column_name
        if not col_name:
            continue
        if col_name not in columns_map:
            columns_map[col_name] = []
            column_indices[col_name] = row.column_id
        columns_map[col_name].append(row)

    # Build columns ordered by source spreadsheet position
    columns: list[ColumnReviewData] = []
    for col_name in sorted(columns_map.keys(), key=lambda c: column_indices[c]):
        manifest_rows = columns_map[col_name]
        transformations = [
            _build_transformation(r, col_name, cache) for r in manifest_rows
        ]

        terms_with_changes = sum(1 for t in transformations if t.isChanged)

        columns.append(ColumnReviewData(
            columnKey=col_name,
            columnLabel=format_column_label(col_name),
            sourceColumnIndex=column_indices[col_name],
            termCount=len(transformations),
            termsWithChanges=terms_with_changes,
            transformations=transformations,
        ))

    return columns


def _build_transformation(
    row: ManifestRow,
    col_key: str,
    cache: SessionCache,
) -> Transformation:
    """Build a Transformation from a manifest row."""
    original_value = row.to_harmonize or ""
    harmonized_value = row.top_harmonization or None
    confidence = row.confidence_score
    is_changed = is_value_changed(original_value, harmonized_value)

    recommendation_type = _compute_recommendation_type(original_value, harmonized_value)

    if confidence is not None:
        bucket = confidence_bucket(confidence)
    else:
        bucket = ConfidenceBucket.LOW if is_changed else ConfidenceBucket.HIGH
        confidence = CONFIDENCE.HIGH if bucket == ConfidenceBucket.HIGH else CONFIDENCE.LOW

    manual_override = get_latest_override_value(row.manual_overrides)
    current_value = manual_override if manual_override else harmonized_value

    pv_set = cache.get_pvs_for_column(col_key)
    pv_available = pv_set is not None and len(pv_set) > 0
    is_conformant = check_value_conformance(current_value, pv_set)

    top_suggestions = _build_suggestions_with_conformance(row.top_harmonizations, pv_set)

    # Convert 0-based manifest indices to 1-based for frontend
    manifest_indices_full = [idx + 1 for idx in row.row_indices]
    row_count = len(manifest_indices_full)
    manifest_indices = manifest_indices_full if row_count <= 50 else manifest_indices_full[:10]

    return Transformation(
        originalValue=original_value,
        harmonizedValue=harmonized_value,
        bucket=bucket.value,
        confidence=confidence,
        isChanged=is_changed,
        recommendationType=recommendation_type.value,
        manualOverride=manual_override,
        isPVConformant=is_conformant,
        pvSetAvailable=pv_available,
        topSuggestions=top_suggestions,
        rowIndices=manifest_indices,
        rowCount=row_count,
    )


def _build_column_pvs(columns: list[_ColumnInfo], file_id: str) -> dict[str, list[str]]:
    """Alphabetical sort ensures predictable dropdown ordering across page loads."""
    cache = ensure_pvs_loaded(file_id)
    column_pvs: dict[str, list[str]] = {}
    columns_without_pvs: list[str] = []

    for col_info in columns:
        pv_set = cache.get_pvs_for_column(col_info.column_name)
        if pv_set:
            column_pvs[col_info.column_name] = sorted(pv_set)
        else:
            columns_without_pvs.append(col_info.column_name)

    # Surface PV availability for debugging
    pv_summary = {k: len(v) for k, v in column_pvs.items()}
    logger.info(
        "Built column PVs",
        extra={
            "file_id": file_id,
            "columns_with_pvs": len(column_pvs),
            "columns_without_pvs": columns_without_pvs[:5] if columns_without_pvs else [],
            "pv_counts": pv_summary,
        },
    )

    if not column_pvs and columns:
        logger.warning(
            "No PVs available for any column. PV combobox will not appear in Stage 4.",
            extra={"file_id": file_id, "column_count": len(columns)},
        )

    return column_pvs


def _build_suggestions_with_conformance(
    suggestions: list[str],
    pv_set: frozenset[str] | None,
) -> list[SuggestionInfo]:
    """Flag each suggestion's PV conformance for UI indicator display."""
    if not suggestions:
        return []
    return [
        SuggestionInfo(value=s, isPVConformant=check_value_conformance(s, pv_set))
        for s in suggestions
    ]


def _compute_recommendation_type(
    original_value: str | None,
    harmonized_value: str | None,
) -> RecommendationType:
    """Whitespace-only values mean no useful recommendation; comparisons preserve whitespace."""
    if not harmonized_value or not harmonized_value.strip():
        return RecommendationType.NO_RECOMMENDATION

    # Compare as-is (whitespace significant per domain rules)
    original = original_value or ""
    if original != harmonized_value:
        return RecommendationType.AI_CHANGED

    return RecommendationType.AI_UNCHANGED


def _load_manifest(storage: UploadStorage, file_id: str) -> ManifestSummary | None:
    manifest_path = storage.load_harmonization_manifest_path(file_id)
    if manifest_path is None:
        return None
    return read_manifest_parquet(manifest_path)


FileIdPath = Annotated[str, Path(min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)]


@stage_four_router.get(
    "/overrides/{file_id}",
    response_model=ReviewOverridesSchema | None,
    name="stage_four_get_overrides",
)
async def get_overrides(file_id: FileIdPath) -> ReviewOverridesSchema | None:
    store = get_file_store()
    data = store.load(file_id, FileType.REVIEW_OVERRIDES)
    if data is None:
        return None
    return ReviewOverridesSchema(
        file_id=data.get("file_id", file_id),
        created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(UTC),
        updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(UTC),
        overrides=data.get("overrides", {}),
        review_state=ReviewStateSchema(**data.get("review_state", {})),
    )


@stage_four_router.post("/overrides", response_model=SaveOverridesResponse, name="stage_four_save_overrides")
async def save_overrides(payload: SaveOverridesRequest) -> SaveOverridesResponse:
    store = get_file_store()
    storage = get_upload_storage()
    now = datetime.now(UTC)

    existing = store.load(payload.file_id, FileType.REVIEW_OVERRIDES)
    created_at = existing.get("created_at") if existing else now.isoformat()

    data = {
        "file_id": payload.file_id,
        "created_at": created_at,
        "updated_at": now.isoformat(),
        "overrides": {
            row_key: {col: override.model_dump() for col, override in cols.items()}
            for row_key, cols in payload.overrides.items()
        },
        "review_state": payload.review_state.model_dump(),
    }
    store.save(payload.file_id, FileType.REVIEW_OVERRIDES, data)

    _sync_overrides_to_manifest(storage, payload)

    return SaveOverridesResponse(file_id=payload.file_id, updated_at=now)


def _sync_overrides_to_manifest(storage: UploadStorage, payload: SaveOverridesRequest) -> None:
    manifest_path = storage.load_harmonization_manifest_path(payload.file_id)
    if manifest_path is None:
        logger.warning("Cannot sync overrides: manifest path not found", extra={"file_id": payload.file_id})
        return

    overrides_batch = _collect_overrides_for_batch(payload)
    if not overrides_batch:
        return

    add_manual_overrides_batch(
        manifest_path=manifest_path,
        overrides=overrides_batch,
        user_id=None,
    )


def _collect_overrides_for_batch(
    payload: SaveOverridesRequest,
) -> list[tuple[str, str, str]]:
    """Deduplicate by (col, original, value) to avoid duplicate ManualOverride entries."""
    seen: set[tuple[str, str, str]] = set()
    overrides: list[tuple[str, str, str]] = []
    for _row_key, cols in payload.overrides.items():
        for col_key, override in cols.items():
            if override.original_value is None:
                continue
            key = (col_key, override.original_value, override.human_value)
            if key not in seen:
                seen.add(key)
                overrides.append(key)
    return overrides


@stage_four_router.delete(
    "/overrides/{file_id}",
    response_model=DeleteOverridesResponse,
    name="stage_four_delete_overrides",
)
async def delete_overrides(file_id: FileIdPath) -> DeleteOverridesResponse:
    store = get_file_store()
    existed = store.exists(file_id, FileType.REVIEW_OVERRIDES)
    store.delete(file_id, FileType.REVIEW_OVERRIDES)
    return DeleteOverridesResponse(file_id=file_id, deleted=existed)


@stage_four_router.get(
    "/non-conformant/{file_id}",
    response_model=NonConformantResponse,
    name="stage_four_non_conformant",
)
async def get_non_conformant_values(file_id: FileIdPath) -> NonConformantResponse:
    """Deduplicate by (column, original, final) to match Stage 5's unique mapping logic."""
    storage = get_upload_storage()
    cache = ensure_pvs_loaded(file_id)
    manifest = _load_manifest(storage, file_id)

    if manifest is None:
        return NonConformantResponse(count=0, items=[])

    # Track unique (column, original, final) tuples to avoid counting duplicates
    seen: set[tuple[str, str, str]] = set()
    non_conformant: list[NonConformantItem] = []

    for row in manifest.rows:
        # Get the current value (latest override > AI harmonization)
        latest_override = get_latest_override_value(row.manual_overrides)
        current_value = latest_override if latest_override else row.top_harmonization

        # Skip if we've already processed this exact mapping
        key = (row.column_name, row.to_harmonize, current_value or "")
        if key in seen:
            continue
        seen.add(key)

        # Check PV conformance using shared function for consistent behavior
        pv_set = cache.get_pvs_for_column(row.column_name)
        if pv_set and current_value and not check_value_conformance(current_value, pv_set):
            non_conformant.append(NonConformantItem(
                column=row.column_name,
                value=current_value,
                original=row.to_harmonize,
            ))

    return NonConformantResponse(
        count=len(non_conformant),
        items=non_conformant,
    )


@stage_four_router.post(
    "/row-context",
    response_model=RowContextResponse,
    name="stage_four_row_context",
)
async def get_row_context(payload: RowContextRequest) -> RowContextResponse:
    """On-demand fetch avoids loading full spreadsheet into review state."""
    storage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found")

    headers, original_rows = load_csv(meta.saved_path)

    selected_rows: list[list[str]] = []
    for idx in payload.row_indices:
        if 0 <= idx < len(original_rows):
            row_dict = original_rows[idx]
            row_values = [row_dict.get(h, "") for h in headers]
            selected_rows.append(row_values)

    return RowContextResponse(headers=headers, rows=selected_rows)


class TermRowIndicesRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)
    column_key: str
    original_value: str


class TermRowIndicesResponse(BaseModel):
    row_indices: list[int]  # 0-based indices for API consistency


@stage_four_router.post(
    "/term-row-indices",
    response_model=TermRowIndicesResponse,
    name="stage_four_term_row_indices",
)
async def get_term_row_indices(payload: TermRowIndicesRequest) -> TermRowIndicesResponse:
    """Fetch full row indices for a term when truncated in initial response."""
    storage = get_upload_storage()
    manifest = _load_manifest(storage, payload.file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Manifest not found")

    for row in manifest.rows:
        if row.column_name == payload.column_key and row.to_harmonize == payload.original_value:
            return TermRowIndicesResponse(row_indices=row.row_indices)

    return TermRowIndicesResponse(row_indices=[])
