"""
Provide the final download step for harmonized data.

Generate downloadable zip files containing harmonized CSV and parquet manifest,
and compute summary statistics comparing original vs harmonized datasets.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from csv import DictReader
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.domain import SessionKey
from src.domain.storage import FileType
from src.stage_1_upload.dependencies import get_file_store, get_upload_storage
from src.stage_1_upload.services import UploadStorage

_logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"
STAGE_FIVE_STATIC_PATH = _MODULE_DIR / "static"

_HARMONIZED_SUFFIX = ".harmonized.csv"

_ERROR_UPLOAD_NOT_FOUND = "Upload not found. Please restart the harmonization process."
_ERROR_DATASET_NOT_FOUND = "Required dataset file not found."
_ERROR_HARMONIZED_NOT_FOUND = "Harmonized file not found. Please rerun Stage 3."
_ERROR_DATASET_UNREADABLE = "Unable to read harmonized dataset."

_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

stage_five_router = APIRouter(prefix="/stage-5", tags=["Stage 5 Download"])


class StageFiveRequest(BaseModel):
    """why: unified request payload for summary and download operations."""

    file_id: str
    manual_columns: list[str] = []


class ColumnSummary(BaseModel):
    """why: per-column metrics for the harmonization summary UI."""

    column: str
    distinct_terms: int
    ai_changes: int
    manual_changes: int


class StageFiveSummaryResponse(BaseModel):
    """why: response shape for the summary endpoint consumed by frontend."""

    column_summaries: list[ColumnSummary]


@stage_five_router.get("", response_class=HTMLResponse, name="stage_five_review_page")
async def render_stage_five(request: Request) -> HTMLResponse:
    """why: serve the download page with all navigation URLs pre-resolved."""
    context = {
        "request": request,
        "stage_one_url": request.url_for("stage_one_upload_page"),
        "stage_two_url": request.url_for("stage_two_mapping_page"),
        "stage_three_url": request.url_for("stage_three_entry"),
        "stage_four_url": request.url_for("stage_four_review_page"),
        "stage_three_payload_key": SessionKey.STAGE_THREE_PAYLOAD.value,
        "summary_endpoint": request.url_for("stage_five_summary"),
        "download_endpoint": request.url_for("stage_five_download"),
    }
    return _templates.TemplateResponse("stage_5_review.html", context)


@stage_five_router.post("/summary", response_model=StageFiveSummaryResponse, name="stage_five_summary")
async def summarize_harmonized_results(payload: StageFiveRequest) -> StageFiveSummaryResponse:
    """why: compute change statistics by comparing original and harmonized CSVs."""
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail=_ERROR_UPLOAD_NOT_FOUND)

    harmonized_path = _resolve_harmonized_path(meta.saved_path, payload.file_id)
    _, original_rows = _load_csv(meta.saved_path)
    headers, harmonized_rows = _load_csv(harmonized_path)
    if not headers:
        raise HTTPException(status_code=400, detail=_ERROR_DATASET_UNREADABLE)

    manual_set = {col.strip().lower() for col in payload.manual_columns if col and col.strip()}
    return _build_summary(headers, original_rows, harmonized_rows, manual_set)


@stage_five_router.post("/download", name="stage_five_download")
async def download_harmonized_data(payload: StageFiveRequest) -> StreamingResponse:
    """why: bundle final CSV (with manual overrides applied) and manifest into a zip."""
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail=_ERROR_UPLOAD_NOT_FOUND)

    harmonized_path = _resolve_harmonized_path(meta.saved_path, payload.file_id)
    manifest_path = storage.load_harmonization_manifest_path(payload.file_id)

    headers, harmonized_rows = _load_csv(harmonized_path)
    if not headers:
        raise HTTPException(status_code=400, detail=_ERROR_DATASET_UNREADABLE)

    overrides = _load_review_overrides(payload.file_id)
    final_rows = _apply_overrides(harmonized_rows, overrides)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    original_stem = Path(meta.original_name).stem
    base_name = f"{original_stem}_{payload.file_id}_{timestamp}"

    zip_buffer = _create_zip_buffer(base_name, headers, final_rows, manifest_path)
    return _create_streaming_response(base_name, zip_buffer)


def _resolve_harmonized_path(original_path: Path, file_id: str) -> Path:
    """why: harmonized files may be stored in different locations depending on the pipeline."""
    candidates = [
        original_path.with_name(f"{original_path.stem}{_HARMONIZED_SUFFIX}"),
        original_path.with_suffix(original_path.suffix + _HARMONIZED_SUFFIX),
        Path.cwd() / f"{file_id}{_HARMONIZED_SUFFIX}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise HTTPException(status_code=404, detail=_ERROR_HARMONIZED_NOT_FOUND)


def _load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """why: read CSV into memory for comparison and transformation."""
    if not path.exists():
        raise HTTPException(status_code=404, detail=_ERROR_DATASET_NOT_FOUND)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = DictReader(handle)
        rows = list(reader)
        headers = list(reader.fieldnames) if reader.fieldnames else []
    return headers, rows


def _load_review_overrides(file_id: str) -> dict[str, dict[str, str]]:
    """why: retrieve manual corrections made during Stage 4 review."""
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


def _apply_overrides(
    rows: list[dict[str, str]],
    overrides: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """why: layer manual corrections on top of AI harmonization results.

    Row keys are 1-indexed to match the Stage 4 review UI row numbering.
    """
    if not overrides:
        return rows

    result: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        row_key = str(idx + 1)
        if row_key not in overrides:
            result.append(row)
            continue

        row_overrides = overrides[row_key]
        invalid_columns = {k for k in row_overrides if k not in row}
        if invalid_columns:
            _logger.warning("Row %s has overrides for non-existent columns: %s", row_key, invalid_columns)

        valid_overrides = {k: v for k, v in row_overrides.items() if k in row}
        result.append({**row, **valid_overrides})
    return result


def _rows_to_csv_bytes(headers: list[str], rows: list[dict[str, str]]) -> str:
    """why: serialize rows to CSV format for inclusion in zip archive."""
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
    """why: package CSV and parquet into a zip archive."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_content = _rows_to_csv_bytes(headers, rows)
        zf.writestr(f"{base_name}.csv", csv_content)

        if manifest_path and manifest_path.exists():
            zf.write(manifest_path, f"{base_name}.parquet")

    zip_buffer.seek(0)
    return zip_buffer


def _create_streaming_response(base_name: str, zip_buffer: io.BytesIO) -> StreamingResponse:
    """why: wrap zip buffer in HTTP streaming response with proper headers."""
    safe_filename = quote(f"{base_name}.zip", safe="")
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"},
    )


def _tally_cell_change(
    original_value: str,
    harmonized_value: str,
    column: str,
    manual_set: set[str],
    ai_counts: dict[str, int],
    manual_counts: dict[str, int],
) -> None:
    """why: categorize a single cell change as AI or manual."""
    if original_value == harmonized_value:
        return
    if column.lower() in manual_set:
        manual_counts[column] += 1
    else:
        ai_counts[column] += 1


def _compute_column_metrics(
    headers: list[str],
    original_rows: list[dict[str, str]],
    harmonized_rows: list[dict[str, str]],
    manual_set: set[str],
) -> tuple[dict[str, int], dict[str, int], dict[str, set[str]]]:
    """why: calculate AI changes, manual changes, and distinct terms per column."""
    total_rows = min(len(original_rows), len(harmonized_rows))
    ai_counts: dict[str, int] = defaultdict(int)
    manual_counts: dict[str, int] = defaultdict(int)
    distinct_terms: dict[str, set[str]] = defaultdict(set)

    for idx in range(total_rows):
        original_row = original_rows[idx]
        harmonized_row = harmonized_rows[idx]
        for column in headers:
            original_value = (original_row.get(column) or "").strip()
            harmonized_value = (harmonized_row.get(column) or "").strip()
            if original_value:
                distinct_terms[column].add(original_value)
            _tally_cell_change(original_value, harmonized_value, column, manual_set, ai_counts, manual_counts)

    return ai_counts, manual_counts, distinct_terms


def _create_summary_response(
    headers: list[str],
    ai_counts: dict[str, int],
    manual_counts: dict[str, int],
    distinct_terms: dict[str, set[str]],
) -> StageFiveSummaryResponse:
    """why: transform metrics into the summary response shape."""
    return StageFiveSummaryResponse(
        column_summaries=[
            ColumnSummary(
                column=column,
                distinct_terms=len(distinct_terms[column]),
                ai_changes=ai_counts[column],
                manual_changes=manual_counts[column],
            )
            for column in headers
        ]
    )


def _build_summary(
    headers: list[str],
    original_rows: list[dict[str, str]],
    harmonized_rows: list[dict[str, str]],
    manual_set: set[str],
) -> StageFiveSummaryResponse:
    """why: aggregate change counts per column for the summary UI."""
    ai_counts, manual_counts, distinct_terms = _compute_column_metrics(
        headers, original_rows, harmonized_rows, manual_set
    )
    return _create_summary_response(headers, ai_counts, manual_counts, distinct_terms)
