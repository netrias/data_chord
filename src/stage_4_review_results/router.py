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
from src.domain.data_model_cache import ensure_pvs_loaded, get_session_cache
from src.domain.dependencies import get_file_store, get_upload_storage
from src.domain.manifest import (
    ConfidenceBucket,
    ManifestSummary,
    ManualOverride,
    add_manual_overrides_batch,
    confidence_bucket,
    get_latest_override_value,
    is_value_changed,
    read_manifest_parquet,
)
from src.domain.pv_validation import check_value_conformance
from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN
from src.domain.storage import FileType, UploadStorage, load_csv
from src.stage_4_review_results.schemas import (
    DeleteOverridesResponse,
    NonConformantItem,
    NonConformantResponse,
    ReviewOverridesSchema,
    ReviewStateSchema,
    RowContextRequest,
    RowContextResponse,
    SaveOverridesRequest,
    SaveOverridesResponse,
)

logger = logging.getLogger(__name__)

_MODULE_DIR = FilePath(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"


_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


class StageFourResultsRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)
    manual_columns: list[str] = []


class SuggestionInfo(BaseModel):
    value: str
    isPVConformant: bool


class StageFourCell(BaseModel):
    columnKey: str
    columnLabel: str
    originalValue: str | None
    harmonizedValue: str | None
    bucket: str
    confidence: float
    isChanged: bool
    recommendationType: str  # ai_changed, ai_unchanged, no_recommendation
    manualOverride: str | None = None
    isPVConformant: bool = True  # False if current value not in PV set
    pvSetAvailable: bool = False  # True if we have PVs for this column
    topSuggestions: list[SuggestionInfo] = []  # AI suggestions with PV conformance flags


class StageFourRow(BaseModel):
    rowNumber: int
    recordId: str
    cells: list[StageFourCell]
    sourceRowNumber: int | None = None


class StageFourResultsResponse(BaseModel):
    rows: list[StageFourRow]
    columnPVs: dict[str, list[str]] = {}  # column_key -> sorted PV list
    totalOriginalRows: int = 0  # Original spreadsheet row count (before grouping)


@dataclass
class _StageFourRowGroup:
    record_ids: list[str]
    cells: list[StageFourCell]
    source_row_number: int


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
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.")

    _, original_rows = load_csv(meta.saved_path)
    manifest = _load_manifest(storage, payload.file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Harmonization manifest not found. Please rerun Stage 3.")

    columns = _extract_columns_from_manifest(manifest)
    row_lookup = _build_row_lookup(manifest)
    rows = _build_rows_from_manifest(original_rows, columns, row_lookup, payload.file_id)
    column_pvs = _build_column_pvs(columns, payload.file_id)
    return StageFourResultsResponse(
        rows=rows,
        columnPVs=column_pvs,
        totalOriginalRows=len(original_rows),
    )


@dataclass
class _ColumnInfo:
    column_name: str
    label: str


@dataclass
class _ManifestEntry:
    to_harmonize: str
    top_harmonization: str
    top_harmonizations: list[str]
    confidence_score: float | None
    row_indices: list[int]
    manual_overrides: list[ManualOverride]


RowLookup = dict[tuple[str, int], _ManifestEntry]


def _extract_columns_from_manifest(manifest: ManifestSummary) -> list[_ColumnInfo]:
    seen: set[str] = set()
    columns: list[_ColumnInfo] = []
    for row in manifest.rows:
        col_name = row.column_name
        if col_name and col_name not in seen:
            seen.add(col_name)
            columns.append(_ColumnInfo(column_name=col_name, label=format_column_label(col_name)))
    return columns


def _build_row_lookup(manifest: ManifestSummary) -> RowLookup:
    lookup: RowLookup = {}
    for row in manifest.rows:
        entry = _ManifestEntry(
            to_harmonize=row.to_harmonize,
            top_harmonization=row.top_harmonization,
            top_harmonizations=row.top_harmonizations,
            confidence_score=row.confidence_score,
            row_indices=row.row_indices,
            manual_overrides=row.manual_overrides,
        )
        for row_idx in row.row_indices:
            lookup[(row.column_name, row_idx)] = entry
    return lookup


def _build_rows_from_manifest(
    original_rows: list[dict[str, str]],
    columns: list[_ColumnInfo],
    row_lookup: RowLookup,
    file_id: str,
) -> list[StageFourRow]:
    total_rows = len(original_rows)
    grouped: dict[tuple[tuple[str, str, str], ...], _StageFourRowGroup] = {}

    for idx in range(total_rows):
        original_row = original_rows[idx]
        record_id = original_row.get("record_id") or f"Row {idx + 1}"
        cells = _build_cells_from_manifest(idx, columns, row_lookup, file_id)
        key = tuple((c.columnKey, c.originalValue or "", c.harmonizedValue or "") for c in cells)

        if key not in grouped:
            grouped[key] = _StageFourRowGroup(record_ids=[record_id], cells=cells, source_row_number=idx + 1)
        else:
            grouped[key].record_ids.append(record_id)

    return [
        StageFourRow(
            rowNumber=group_index,
            recordId=_summarize_record_ids(group.record_ids),
            cells=group.cells,
            sourceRowNumber=group.source_row_number,
        )
        for group_index, group in enumerate(grouped.values(), start=1)
    ]


def _build_column_pvs(columns: list[_ColumnInfo], file_id: str) -> dict[str, list[str]]:
    """Alphabetical sort ensures predictable dropdown ordering across page loads."""
    cache = ensure_pvs_loaded(file_id)
    column_pvs: dict[str, list[str]] = {}
    for col_info in columns:
        pv_set = cache.get_pvs_for_column(col_info.column_name)
        if pv_set:
            column_pvs[col_info.column_name] = sorted(pv_set)
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


def _build_cells_from_manifest(
    row_index: int,
    columns: list[_ColumnInfo],
    row_lookup: RowLookup,
    file_id: str,
) -> list[StageFourCell]:
    cells: list[StageFourCell] = []
    cache = get_session_cache(file_id)

    for col_info in columns:
        col_key = col_info.column_name
        lookup_key = (col_key, row_index)
        entry = row_lookup.get(lookup_key)

        if entry is None:
            continue

        original_value = entry.to_harmonize or None
        harmonized_value = entry.top_harmonization or None
        confidence = entry.confidence_score
        is_changed = is_value_changed(original_value, harmonized_value)

        # Determine recommendation type
        recommendation_type = _compute_recommendation_type(original_value, harmonized_value)

        if confidence is not None:
            bucket = confidence_bucket(confidence)
        else:
            bucket = ConfidenceBucket.LOW if is_changed else ConfidenceBucket.HIGH
            confidence = CONFIDENCE.HIGH if bucket == ConfidenceBucket.HIGH else CONFIDENCE.LOW

        manual_override = get_latest_override_value(entry.manual_overrides)

        # Determine current value for PV conformance check (override > harmonized)
        current_value = manual_override if manual_override else harmonized_value

        # Check PV conformance
        pv_set = cache.get_pvs_for_column(col_key)
        pv_available = pv_set is not None and len(pv_set) > 0
        is_conformant = check_value_conformance(current_value, pv_set)

        # Build suggestions with conformance flags
        top_suggestions = _build_suggestions_with_conformance(entry.top_harmonizations, pv_set)

        cells.append(
            StageFourCell(
                columnKey=col_key,
                columnLabel=col_info.label,
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
            ),
        )

    return cells


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


def _summarize_record_ids(record_ids: list[str]) -> str:
    filtered = [rid for rid in record_ids if rid]
    if not filtered:
        return "Multiple records"
    if len(filtered) == 1:
        return filtered[0]
    return f"{filtered[0]} + {len(filtered) - 1} more"


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
    overrides: list[tuple[str, str, str]] = []
    for _row_key, cols in payload.overrides.items():
        for col_key, override in cols.items():
            if override.original_value is None:
                continue
            overrides.append((col_key, override.original_value, override.human_value))
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
