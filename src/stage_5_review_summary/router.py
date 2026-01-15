"""
HTTP routes for summarizing harmonization results and generating downloads.

Computes change statistics and packages final CSV with manifest for export.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.domain import ChangeType
from src.domain.data_model_cache import SessionCache, ensure_pvs_loaded
from src.domain.dependencies import get_file_store, get_upload_storage
from src.domain.manifest import (
    ManifestRow,
    ManifestSummary,
    get_latest_override_value,
    is_value_changed,
    read_manifest_parquet,
)
from src.domain.pv_validation import check_value_conformance
from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN
from src.domain.storage import FileType, UploadStorage, load_csv, resolve_harmonized_path_or_404

_logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"

_ERROR_UPLOAD_NOT_FOUND = "Upload not found. Please restart the harmonization process."
_ERROR_DATASET_UNREADABLE = "Unable to read harmonized dataset."
_ERROR_MANIFEST_NOT_FOUND = "Harmonization manifest not found. Please rerun Stage 3."
_ERROR_MANIFEST_UNREADABLE = "Unable to read harmonization manifest."

_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

stage_five_router = APIRouter(prefix="/stage-5", tags=["Stage 5 Download"])


class StageFiveRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)


class ColumnSummary(BaseModel):
    column: str
    distinct_terms: int
    ai_changes: int
    manual_changes: int
    unchanged: int


class TransformationStep(BaseModel):
    value: str
    source: str  # "original", "ai", "user", "system"
    timestamp: str | None = None
    user_id: str | None = None


class TermMapping(BaseModel):
    column: str
    original_value: str
    final_value: str
    is_pv_conformant: bool = True
    history: list[TransformationStep] = []


class StageFiveSummaryResponse(BaseModel):
    column_summaries: list[ColumnSummary]
    term_mappings: list[TermMapping]
    non_conformant_count: int = 0


@stage_five_router.get("", response_class=HTMLResponse, name="stage_five_review_page")
async def render_stage_five(request: Request) -> HTMLResponse:
    context: dict[str, Any] = {
        "request": request,
        "stage_one_url": request.url_for("stage_one_upload_page"),
        "stage_two_url": request.url_for("stage_two_mapping_page"),
        "stage_three_url": request.url_for("stage_three_entry"),
        "stage_four_url": request.url_for("stage_four_review_page"),
        "summary_endpoint": str(request.url_for("stage_five_summary")),
        "download_endpoint": str(request.url_for("stage_five_download")),
    }
    return _templates.TemplateResponse("stage_5_review.html", context)


@stage_five_router.post("/summary", response_model=StageFiveSummaryResponse, name="stage_five_summary")
async def summarize_harmonized_results(payload: StageFiveRequest) -> StageFiveSummaryResponse:
    storage: UploadStorage = get_upload_storage()
    manifest_path = storage.load_harmonization_manifest_path(payload.file_id)
    if manifest_path is None:
        raise HTTPException(status_code=404, detail=_ERROR_MANIFEST_NOT_FOUND)

    manifest_summary = read_manifest_parquet(manifest_path)
    if manifest_summary is None:
        raise HTTPException(status_code=400, detail=_ERROR_MANIFEST_UNREADABLE)

    return _build_summary_from_manifest(manifest_summary, payload.file_id)


@stage_five_router.post("/download", name="stage_five_download")
async def download_harmonized_data(payload: StageFiveRequest) -> StreamingResponse:
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail=_ERROR_UPLOAD_NOT_FOUND)

    harmonized_path = resolve_harmonized_path_or_404(meta.saved_path, payload.file_id)
    manifest_path = storage.load_harmonization_manifest_path(payload.file_id)

    headers, harmonized_rows = load_csv(harmonized_path)
    if not headers:
        raise HTTPException(status_code=400, detail=_ERROR_DATASET_UNREADABLE)

    overrides = _load_review_overrides(payload.file_id)
    final_rows = _apply_overrides(harmonized_rows, overrides)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    original_stem = Path(meta.original_name).stem
    base_name = f"{original_stem}_{payload.file_id}_{timestamp}"

    zip_buffer = _create_zip_buffer(base_name, headers, final_rows, manifest_path)

    return _create_streaming_response(base_name, zip_buffer)


def _load_review_overrides(file_id: str) -> dict[str, dict[str, str]]:
    store = get_file_store()
    data = store.load(file_id, FileType.REVIEW_OVERRIDES)
    if data is None:
        return {}

    result: dict[str, dict[str, str]] = {}
    overrides_data: dict[str, Any] = data.get("overrides", {})
    for row_key, columns in overrides_data.items():
        result[row_key] = {
            col: info["human_value"]
            for col, info in columns.items()
            if info.get("human_value") is not None
        }
    return result


def _apply_row_overrides(row: dict[str, str], row_key: str, row_overrides: dict[str, str]) -> dict[str, str]:
    invalid_columns = {k for k in row_overrides if k not in row}
    if invalid_columns:
        _logger.warning("Row %s has overrides for non-existent columns: %s", row_key, invalid_columns)
    valid_overrides = {k: v for k, v in row_overrides.items() if k in row}
    return {**row, **valid_overrides}


def _apply_overrides(
    rows: list[dict[str, str]],
    overrides: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """Row keys are 1-indexed to match Stage 4 UI numbering."""
    if not overrides:
        return rows

    result: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        row_key = str(idx + 1)
        if row_key in overrides:
            result.append(_apply_row_overrides(row, row_key, overrides[row_key]))
        else:
            result.append(row)
    return result


def _rows_to_csv_string(headers: list[str], rows: list[dict[str, str]]) -> str:
    csv_output = io.StringIO()
    writer = csv.DictWriter(csv_output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return csv_output.getvalue()


def _create_zip_buffer(
    base_name: str,
    headers: list[str],
    rows: list[dict[str, str]],
    manifest_path: Path | None,
) -> io.BytesIO:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_content = _rows_to_csv_string(headers, rows)
        zf.writestr(f"{base_name}.csv", csv_content)

        if manifest_path and manifest_path.exists():
            zf.write(manifest_path, f"{base_name}.parquet")

    zip_buffer.seek(0)
    return zip_buffer


def _create_streaming_response(base_name: str, zip_buffer: io.BytesIO) -> StreamingResponse:
    safe_filename = quote(f"{base_name}.zip", safe="")
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"},
    )


def _classify_change(row: ManifestRow) -> ChangeType:
    original = row.to_harmonize
    ai_value = row.top_harmonization
    latest_override = get_latest_override_value(row.manual_overrides)

    final_value = latest_override if latest_override is not None else ai_value

    if not is_value_changed(original, final_value):
        return ChangeType.UNCHANGED

    if latest_override is not None and latest_override != ai_value:
        return ChangeType.MANUAL_OVERRIDE

    return ChangeType.AI_HARMONIZED


def _get_final_value(row: ManifestRow) -> str:
    return get_latest_override_value(row.manual_overrides) or row.top_harmonization


def _build_history(row: ManifestRow) -> list[TransformationStep]:
    """Collapse consecutive overrides with the same value to show only unique steps."""
    steps: list[TransformationStep] = []

    steps.append(TransformationStep(value=row.to_harmonize, source="original"))

    if row.top_harmonization != row.to_harmonize:
        steps.append(TransformationStep(value=row.top_harmonization, source="ai"))

    last_override_value: str | None = None
    for override in row.manual_overrides:
        if override.value == last_override_value:
            continue
        last_override_value = override.value
        steps.append(
            TransformationStep(
                value=override.value,
                source="user",
                timestamp=override.timestamp,
                user_id=override.user_id,
            )
        )

    if row.pv_adjustment is not None:
        steps.append(
            TransformationStep(
                value=row.pv_adjustment.adjusted_value,
                source="system",
                timestamp=row.pv_adjustment.timestamp,
                user_id=row.pv_adjustment.user_id,
            )
        )

    return steps


class _MappingInfo(NamedTuple):
    """Immutable container for conformance result and transformation history."""

    is_conformant: bool
    history: list[TransformationStep]


def _process_manifest_row(
    row: ManifestRow,
    ai_counts: dict[int, int],
    manual_counts: dict[int, int],
    unchanged_counts: dict[int, int],
    unique_mappings: dict[tuple[str, str, str], _MappingInfo],
    cache: SessionCache,
) -> None:
    col_id = row.column_id
    change_type = _classify_change(row)

    match change_type:
        case ChangeType.AI_HARMONIZED:
            ai_counts[col_id] += 1
        case ChangeType.MANUAL_OVERRIDE:
            manual_counts[col_id] += 1
        case ChangeType.UNCHANGED:
            unchanged_counts[col_id] += 1

    # Track all rows for conformance checking, not just changed ones
    _track_mapping(unique_mappings, row, cache)


def _build_summary_from_manifest(summary: ManifestSummary, file_id: str) -> StageFiveSummaryResponse:
    cache = ensure_pvs_loaded(file_id)
    ai_counts: dict[int, int] = defaultdict(int)
    manual_counts: dict[int, int] = defaultdict(int)
    unchanged_counts: dict[int, int] = defaultdict(int)
    distinct_terms: dict[int, int] = defaultdict(int)
    column_names: dict[int, str] = {}
    unique_mappings: dict[tuple[str, str, str], _MappingInfo] = {}

    for row in summary.rows:
        distinct_terms[row.column_id] += 1
        column_names[row.column_id] = row.column_name
        _process_manifest_row(row, ai_counts, manual_counts, unchanged_counts, unique_mappings, cache)

    column_ids = sorted(distinct_terms.keys())
    sorted_mappings = sorted(unique_mappings.items(), key=lambda x: x[0])
    term_mappings = [
        TermMapping(
            column=col,
            original_value=orig,
            final_value=final,
            is_pv_conformant=info.is_conformant,
            history=info.history,
        )
        for (col, orig, final), info in sorted_mappings
    ]

    return StageFiveSummaryResponse(
        column_summaries=[
            ColumnSummary(
                column=column_names[col_id],
                distinct_terms=distinct_terms[col_id],
                ai_changes=ai_counts[col_id],
                manual_changes=manual_counts[col_id],
                unchanged=unchanged_counts[col_id],
            )
            for col_id in column_ids
        ],
        term_mappings=term_mappings,
        non_conformant_count=sum(1 for info in unique_mappings.values() if not info.is_conformant),
    )


def _track_mapping(
    mappings: dict[tuple[str, str, str], _MappingInfo],
    row: ManifestRow,
    cache: SessionCache,
) -> None:
    """Deduplicates by (column, original, final) so we check conformance once per unique mapping."""
    # Empty string means no data; whitespace-only values pass through as semantically significant
    if not row.to_harmonize:
        return
    final = _get_final_value(row)
    key = (row.column_name, row.to_harmonize, final)
    if key in mappings:
        return
    pv_set = cache.get_pvs_for_column(row.column_name)
    is_conformant = check_value_conformance(final, pv_set)
    history = _build_history(row)
    mappings[key] = _MappingInfo(is_conformant, history)
