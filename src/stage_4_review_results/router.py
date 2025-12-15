"""
Serve Stage 4 review and approval routes.
Render the batch review UI and provide harmonized row data for approval.
"""

from __future__ import annotations

import logging
from csv import DictReader
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.domain import CONFIDENCE, DEFAULT_TARGET_SCHEMA, SessionKey
from src.domain.dependencies import get_file_store, get_upload_storage
from src.domain.manifest import (
    ManifestSummary,
    ManualOverride,
    add_manual_overrides_batch,
    confidence_bucket,
    read_manifest_parquet,
)
from src.domain.storage import FileType, UploadStorage
from src.stage_4_review_results.schemas import (
    DeleteOverridesResponse,
    ReviewOverridesSchema,
    ReviewStateSchema,
    SaveOverridesRequest,
    SaveOverridesResponse,
)

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"
STAGE_FOUR_STATIC_PATH = _MODULE_DIR / "static"

_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


class StageFourResultsRequest(BaseModel):
    """why: request payload for fetching harmonized rows."""

    file_id: str
    manual_columns: list[str] = []


class StageFourCell(BaseModel):
    """why: represent a single cell comparison in the review UI."""

    columnKey: str
    columnLabel: str
    originalValue: str | None
    harmonizedValue: str | None
    bucket: str
    confidence: float
    isChanged: bool
    manualOverride: str | None = None


class StageFourRow(BaseModel):
    """why: represent a grouped row of cells for review."""

    rowNumber: int
    recordId: str
    cells: list[StageFourCell]
    sourceRowNumber: int | None = None


class StageFourResultsResponse(BaseModel):
    """why: response payload containing all harmonized rows."""

    rows: list[StageFourRow]


@dataclass
class _StageFourRowGroup:
    """why: capture grouped record metadata before serialization."""

    record_ids: list[str]
    cells: list[StageFourCell]
    source_row_number: int


stage_four_router = APIRouter(prefix="/stage-4", tags=["Stage 4 Review"])


@stage_four_router.get("", response_class=HTMLResponse, name="stage_four_review_page")
async def render_stage_four(request: Request) -> HTMLResponse:
    """why: serve the batch review and approval UI."""
    context = {
        "request": request,
        "default_schema": DEFAULT_TARGET_SCHEMA,
        "stage_three_payload_key": SessionKey.STAGE_THREE_PAYLOAD.value,
        "stage_three_job_key": SessionKey.STAGE_THREE_JOB.value,
        "stage_two_url": request.url_for("stage_two_mapping_page"),
        "stage_three_url": request.url_for("stage_three_entry"),
        "results_endpoint": request.url_for("stage_four_harmonized_rows"),
    }
    return _templates.TemplateResponse("stage_4_review.html", context)


@stage_four_router.post("/rows", response_model=StageFourResultsResponse, name="stage_four_harmonized_rows")
async def fetch_stage_four_rows(payload: StageFourResultsRequest) -> StageFourResultsResponse:
    """why: load manifest-driven harmonization data for review."""
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.")

    _, original_rows = _load_csv(meta.saved_path)
    manifest = _load_manifest(storage, payload.file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Harmonization manifest not found. Please rerun Stage 3.")

    columns = _extract_columns_from_manifest(manifest)
    row_lookup = _build_row_lookup(manifest)
    rows = _build_rows_from_manifest(original_rows, columns, row_lookup)
    return StageFourResultsResponse(rows=rows)


def _resolve_harmonized_path(original_path: Path, file_id: str) -> Path:
    """why: locate the harmonized CSV using multiple naming conventions."""
    candidate = original_path.with_name(f"{original_path.stem}.harmonized.csv")
    if candidate.exists():
        return candidate

    suffix_candidate = original_path.with_suffix(original_path.suffix + ".harmonized.csv")
    if suffix_candidate.exists():
        return suffix_candidate

    root_candidate = Path.cwd() / f"{file_id}.harmonized.csv"
    if root_candidate.exists():
        return root_candidate

    raise HTTPException(status_code=404, detail="Harmonized file not found. Please rerun Stage 3.")


def _load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """why: read CSV into headers and row dictionaries."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = DictReader(handle)
        rows = list(reader)
        headers = list(reader.fieldnames or [])
    return headers, rows


@dataclass
class _ColumnInfo:
    """why: metadata for a harmonized column derived from manifest."""

    column_name: str
    label: str


@dataclass
class _ManifestEntry:
    """why: harmonization data for a specific (column, original_value) pair."""

    to_harmonize: str
    top_harmonization: str
    top_harmonizations: list[str]
    confidence_score: float | None
    row_indices: list[int]
    manual_overrides: list[ManualOverride]


RowLookup = dict[tuple[str, int], _ManifestEntry]


def _extract_columns_from_manifest(manifest: ManifestSummary) -> list[_ColumnInfo]:
    """why: derive column list and labels from manifest data."""
    seen: set[str] = set()
    columns: list[_ColumnInfo] = []
    for row in manifest.rows:
        col_name = row.column_name
        if col_name and col_name not in seen:
            seen.add(col_name)
            label = col_name.replace("_", " ").title()
            columns.append(_ColumnInfo(column_name=col_name, label=label))
    return columns


def _build_row_lookup(manifest: ManifestSummary) -> RowLookup:
    """why: create lookup from (column_name, row_index) to manifest entry."""
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
) -> list[StageFourRow]:
    """why: generate grouped rows for Stage 4 review using manifest data."""
    total_rows = len(original_rows)
    grouped: dict[tuple[tuple[str, str, str], ...], _StageFourRowGroup] = {}

    for idx in range(total_rows):
        original_row = original_rows[idx]
        record_id = original_row.get("record_id") or f"Row {idx + 1}"
        cells = _build_cells_from_manifest(idx, columns, row_lookup)
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


def _build_cells_from_manifest(
    row_index: int,
    columns: list[_ColumnInfo],
    row_lookup: RowLookup,
) -> list[StageFourCell]:
    """why: create cell comparisons for a single row using manifest data."""
    cells: list[StageFourCell] = []

    for col_info in columns:
        col_key = col_info.column_name
        lookup_key = (col_key, row_index)
        entry = row_lookup.get(lookup_key)

        if entry is None:
            continue

        original_value = entry.to_harmonize.strip() or None
        harmonized_value = entry.top_harmonization.strip() or None
        confidence = entry.confidence_score
        is_changed = (original_value or "") != (harmonized_value or "")

        if confidence is not None:
            bucket = confidence_bucket(confidence)
        else:
            bucket = "low" if is_changed else "high"
            confidence = CONFIDENCE.HIGH if bucket == "high" else CONFIDENCE.LOW

        manual_override = _get_latest_override(entry.manual_overrides)

        cells.append(
            StageFourCell(
                columnKey=col_key,
                columnLabel=col_info.label,
                originalValue=original_value,
                harmonizedValue=harmonized_value,
                bucket=bucket,
                confidence=confidence,
                isChanged=is_changed,
                manualOverride=manual_override,
            ),
        )

    return cells


def _get_latest_override(overrides: list[ManualOverride]) -> str | None:
    """why: extract the most recent manual override value."""
    if not overrides:
        return None
    return overrides[-1].value


def _summarize_record_ids(record_ids: list[str]) -> str:
    """why: provide a compact label for grouped rows."""
    filtered = [rid for rid in record_ids if rid]
    if not filtered:
        return "Multiple records"
    if len(filtered) == 1:
        return filtered[0]
    return f"{filtered[0]} + {len(filtered) - 1} more"


def _load_manifest(storage: UploadStorage, file_id: str) -> ManifestSummary | None:
    """why: load the harmonization manifest for a file."""
    manifest_path = storage.load_harmonization_manifest_path(file_id)
    if manifest_path is None:
        return None
    return read_manifest_parquet(manifest_path)


@stage_four_router.get(
    "/overrides/{file_id}",
    response_model=ReviewOverridesSchema | None,
    name="stage_four_get_overrides",
)
async def get_overrides(file_id: str) -> ReviewOverridesSchema | None:
    """why: load existing review overrides for a file."""
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
    """why: persist human review overrides to JSON storage and parquet manifest."""
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
    """why: write manual overrides from UI back to parquet manifest."""
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
    """why: gather all overrides into a list for batch processing."""
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
async def delete_overrides(file_id: str) -> DeleteOverridesResponse:
    """why: remove review overrides for a file."""
    store = get_file_store()
    existed = store.exists(file_id, FileType.REVIEW_OVERRIDES)
    store.delete(file_id, FileType.REVIEW_OVERRIDES)
    return DeleteOverridesResponse(file_id=file_id, deleted=existed)
