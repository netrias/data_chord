"""
Provide the final download step for harmonized data.

Generate downloadable zip files containing harmonized CSV and parquet manifest,
and compute summary statistics from the harmonization manifest.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.domain import ChangeType, SessionKey
from src.domain.dependencies import get_file_store, get_upload_storage
from src.domain.manifest import (
    ManifestRow,
    ManifestSummary,
    get_latest_override_value,
    is_value_changed,
    read_manifest_parquet,
)
from src.domain.storage import FileType, UploadStorage, load_csv, resolve_harmonized_path_or_404

_logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"
STAGE_FIVE_STATIC_PATH = _MODULE_DIR / "static"

_ERROR_UPLOAD_NOT_FOUND = "Upload not found. Please restart the harmonization process."
_ERROR_DATASET_UNREADABLE = "Unable to read harmonized dataset."
_ERROR_MANIFEST_NOT_FOUND = "Harmonization manifest not found. Please rerun Stage 3."
_ERROR_MANIFEST_UNREADABLE = "Unable to read harmonization manifest."

_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

stage_five_router = APIRouter(prefix="/stage-5", tags=["Stage 5 Download"])


class StageFiveRequest(BaseModel):
    """why: unified request payload for summary and download operations."""

    file_id: str = Field(..., min_length=8, pattern=r"^[a-f0-9]+$")


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
    """why: compute change statistics from the harmonization manifest parquet."""
    storage: UploadStorage = get_upload_storage()
    manifest_path = storage.load_harmonization_manifest_path(payload.file_id)
    if manifest_path is None:
        raise HTTPException(status_code=404, detail=_ERROR_MANIFEST_NOT_FOUND)

    manifest_summary = read_manifest_parquet(manifest_path)
    if manifest_summary is None:
        raise HTTPException(status_code=400, detail=_ERROR_MANIFEST_UNREADABLE)

    return _build_summary_from_manifest(manifest_summary)


@stage_five_router.post("/download", name="stage_five_download")
async def download_harmonized_data(payload: StageFiveRequest) -> StreamingResponse:
    """why: bundle final CSV (with manual overrides applied) and manifest into a zip."""
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


def _rows_to_csv_string(headers: list[str], rows: list[dict[str, str]]) -> str:
    """why: serialize rows to CSV string for inclusion in zip archive."""
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
        csv_content = _rows_to_csv_string(headers, rows)
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


def _classify_change(row: ManifestRow) -> ChangeType:
    """why: classify a manifest row's change type based on override presence and value.

    Classification logic:
    - UNCHANGED: The final value equals the original (using canonical change detection)
    - AI_HARMONIZED: AI changed the value, or user accepted AI suggestion via override
    - MANUAL_OVERRIDE: User provided an override that differs from AI suggestion

    Uses is_value_changed for consistent comparison across all stages.
    """
    original = row.to_harmonize
    ai_value = row.top_harmonization
    latest_override = get_latest_override_value(row.manual_overrides)

    final_value = latest_override if latest_override is not None else ai_value

    if not is_value_changed(original, final_value):
        return ChangeType.UNCHANGED

    if latest_override is not None and latest_override != ai_value:
        return ChangeType.MANUAL_OVERRIDE

    return ChangeType.AI_HARMONIZED


def _build_summary_from_manifest(summary: ManifestSummary) -> StageFiveSummaryResponse:
    """why: aggregate change counts per column from manifest rows.

    Uses column_id as the key for aggregation since column names may not be unique.
    """
    ai_counts: dict[int, int] = defaultdict(int)
    manual_counts: dict[int, int] = defaultdict(int)
    distinct_terms: dict[int, int] = defaultdict(int)
    column_names: dict[int, str] = {}

    for row in summary.rows:
        col_id = row.column_id
        distinct_terms[col_id] += 1
        column_names[col_id] = row.column_name

        change_type = _classify_change(row)
        match change_type:
            case ChangeType.AI_HARMONIZED:
                ai_counts[col_id] += 1
            case ChangeType.MANUAL_OVERRIDE:
                manual_counts[col_id] += 1
            case ChangeType.UNCHANGED:
                pass

    column_ids = sorted(distinct_terms.keys())
    return StageFiveSummaryResponse(
        column_summaries=[
            ColumnSummary(
                column=column_names[col_id],
                distinct_terms=distinct_terms[col_id],
                ai_changes=ai_counts[col_id],
                manual_changes=manual_counts[col_id],
            )
            for col_id in column_ids
        ]
    )
