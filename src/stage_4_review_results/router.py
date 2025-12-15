"""
Serve Stage 4 review and approval routes.
Render the batch review UI and provide harmonized row data for approval.
"""

from __future__ import annotations

from csv import DictReader
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.domain import CONFIDENCE, SessionKey, get_all_cdes
from src.domain.storage import FileType
from src.stage_1_upload.dependencies import get_file_store, get_upload_storage
from src.stage_1_upload.manifest_reader import confidence_bucket, read_manifest_parquet
from src.stage_1_upload.schemas import DEFAULT_TARGET_SCHEMA
from src.stage_1_upload.services import UploadStorage
from src.stage_4_review_results.schemas import (
    DeleteOverridesResponse,
    ReviewOverridesSchema,
    ReviewStateSchema,
    SaveOverridesRequest,
    SaveOverridesResponse,
)

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
    """why: load and compare original vs harmonized data for review."""
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.")

    harmonized_path = _resolve_harmonized_path(meta.saved_path, payload.file_id)
    _, original_rows = _load_csv(meta.saved_path)
    _, harmonized_rows = _load_csv(harmonized_path)
    manifest_lookup = _build_manifest_lookup(storage, payload.file_id)
    rows = _build_rows(original_rows, harmonized_rows, payload.manual_columns, manifest_lookup)
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


ManifestLookup = dict[tuple[str, str], float]


def _build_rows(
    original_rows: list[dict[str, str]],
    harmonized_rows: list[dict[str, str]],
    manual_columns: list[str],
    manifest_lookup: ManifestLookup,
) -> list[StageFourRow]:
    """why: generate grouped rows for Stage 4 review."""
    manual_set = {col.strip().lower() for col in manual_columns if col}
    total_rows = min(len(original_rows), len(harmonized_rows))
    grouped: dict[tuple[tuple[str, str, str], ...], _StageFourRowGroup] = {}

    for idx in range(total_rows):
        original_row = original_rows[idx]
        harmonized_row = harmonized_rows[idx]
        record_id = harmonized_row.get("record_id") or original_row.get("record_id") or f"Row {idx + 1}"
        cells = _build_cells_for_row(original_row, harmonized_row, manual_set, manifest_lookup)
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


def _build_cells_for_row(
    original_row: dict[str, str],
    harmonized_row: dict[str, str],
    manual_set: set[str],
    manifest_lookup: ManifestLookup,
) -> list[StageFourCell]:
    """why: create cell comparisons for a single row using manifest confidence."""
    cells: list[StageFourCell] = []

    for cde_def in get_all_cdes():
        col_key = cde_def.field.value
        original_value = (original_row.get(col_key) or "").strip() or None
        harmonized_value = (harmonized_row.get(col_key) or "").strip() or None
        is_changed = (original_value or "") != (harmonized_value or "")
        is_manual = col_key.lower() in manual_set and is_changed

        if is_manual:
            confidence = CONFIDENCE.MANUAL
            bucket = "low"
        else:
            lookup_key = (col_key, original_value or "")
            manifest_confidence = manifest_lookup.get(lookup_key)
            if manifest_confidence is not None:
                confidence = manifest_confidence
                bucket = confidence_bucket(confidence)
            else:
                bucket = "low" if is_changed else "high"
                confidence = CONFIDENCE.HIGH if bucket == "high" else CONFIDENCE.LOW

        cells.append(
            StageFourCell(
                columnKey=col_key,
                columnLabel=cde_def.label,
                originalValue=original_value,
                harmonizedValue=harmonized_value,
                bucket=bucket,
                confidence=confidence,
                isChanged=is_changed,
            ),
        )

    return cells


def _summarize_record_ids(record_ids: list[str]) -> str:
    """why: provide a compact label for grouped rows."""
    filtered = [rid for rid in record_ids if rid]
    if not filtered:
        return "Multiple records"
    if len(filtered) == 1:
        return filtered[0]
    return f"{filtered[0]} + {len(filtered) - 1} more"


def _build_manifest_lookup(storage: UploadStorage, file_id: str) -> ManifestLookup:
    """why: create a confidence lookup from stored manifest."""
    manifest_path = storage.load_harmonization_manifest_path(file_id)
    if manifest_path is None:
        return {}
    manifest = read_manifest_parquet(manifest_path)
    if manifest is None:
        return {}
    return {
        (row.column_name, row.to_harmonize): row.confidence_score
        for row in manifest.rows
        if row.confidence_score is not None
    }


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
    """why: persist human review overrides to storage."""
    store = get_file_store()
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
    return SaveOverridesResponse(file_id=payload.file_id, updated_at=now)


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
