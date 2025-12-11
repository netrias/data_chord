"""Serve Stage 4 review and approval routes."""

from __future__ import annotations

from collections.abc import Iterable
from csv import DictReader
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.stage_1_upload.dependencies import get_upload_storage
from src.stage_1_upload.schemas import DEFAULT_TARGET_SCHEMA
from src.stage_1_upload.services import UploadStorage

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_FOUR_STATIC_PATH = MODULE_DIR / "static"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

COLUMN_CONFIG = [
    ("therapeutic_agents", "Therapeutic Agents"),
    ("primary_diagnosis", "Primary Diagnosis"),
    ("morphology", "Morphology"),
    ("tissue_or_organ_of_origin", "Tissue / Organ Origin"),
    ("sample_anatomic_site", "Sample Anatomic Site"),
]


class StageFourResultsRequest(BaseModel):
    file_id: str
    manual_columns: list[str] = []


class StageFourCell(BaseModel):
    columnKey: str
    columnLabel: str
    originalValue: str | None
    harmonizedValue: str | None
    bucket: str
    confidence: float
    isChanged: bool


class StageFourRow(BaseModel):
    rowNumber: int
    recordId: str
    cells: list[StageFourCell]
    sourceRowNumber: int | None = None


class StageFourResultsResponse(BaseModel):
    rows: list[StageFourRow]


@dataclass
class StageFourRowGroup:
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
        "stage_three_payload_key": "stage3HarmonizePayload",
        "stage_three_job_key": "stage3HarmonizeJob",
        "stage_two_url": request.url_for("stage_two_mapping_page"),
        "stage_three_url": request.url_for("stage_three_entry"),
        "results_endpoint": request.url_for("stage_four_harmonized_rows"),
    }
    return _templates.TemplateResponse("stage_4_review.html", context)


@stage_four_router.post("/rows", response_model=StageFourResultsResponse, name="stage_four_harmonized_rows")
async def fetch_stage_four_rows(payload: StageFourResultsRequest) -> StageFourResultsResponse:
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.")
    harmonized_path = _resolve_harmonized_path(meta.saved_path, payload.file_id)
    original_headers, original_rows = _load_csv(meta.saved_path)
    harmonized_headers, harmonized_rows = _load_csv(harmonized_path)
    headers = harmonized_headers or original_headers
    if not headers:
        raise HTTPException(status_code=400, detail="Unable to read dataset headers.")
    rows = _build_rows(headers, original_rows, harmonized_rows, payload.manual_columns)
    return StageFourResultsResponse(rows=rows)


def _resolve_harmonized_path(original_path: Path, file_id: str) -> Path:
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
    with path.open(newline="", encoding="utf-8") as handle:
        reader = DictReader(handle)
        rows = [row for row in reader]
        headers = list(reader.fieldnames or [])
    return headers, rows


def _build_rows(
    _headers: Iterable[str],
    original_rows: list[dict[str, str]],
    harmonized_rows: list[dict[str, str]],
    manual_columns: list[str],
) -> list[StageFourRow]:
    """why: generate grouped rows for Stage 4 review."""

    manual_set = {column.strip().lower() for column in manual_columns if column}
    total_rows = min(len(original_rows), len(harmonized_rows))
    grouped_rows: dict[tuple[tuple[str, str, str], ...], StageFourRowGroup] = {}

    for idx in range(total_rows):
        original_row = original_rows[idx]
        harmonized_row = harmonized_rows[idx]
        record_id = harmonized_row.get("record_id") or original_row.get("record_id") or f"Row {idx + 1}"
        cells: list[StageFourCell] = []
        for column_key, column_label in COLUMN_CONFIG:
            original_value = (original_row.get(column_key) or "").strip() or None
            harmonized_value = (harmonized_row.get(column_key) or "").strip() or None
            is_changed = (original_value or "") != (harmonized_value or "")
            bucket = "high"
            if is_changed:
                bucket = "low"
            is_manual_change = column_key.lower() in manual_set and is_changed
            baseline_confidence = 0.9 if bucket == "high" else 0.3
            confidence = 0.2 if is_manual_change else baseline_confidence
            cells.append(
                StageFourCell(
                    columnKey=column_key,
                    columnLabel=column_label,
                    originalValue=original_value,
                    harmonizedValue=harmonized_value,
                    bucket=bucket,
                    confidence=confidence,
                    isChanged=is_changed,
                ),
            )
        key = tuple((cell.columnKey, cell.originalValue or "", cell.harmonizedValue or "") for cell in cells)
        if key not in grouped_rows:
            grouped_rows[key] = StageFourRowGroup(record_ids=[record_id], cells=cells, source_row_number=idx + 1)
        else:
            grouped_rows[key].record_ids.append(record_id)

    output: list[StageFourRow] = []
    for group_index, group_data in enumerate(grouped_rows.values(), start=1):
        record_label = _summarize_record_ids(group_data.record_ids)
        output.append(
            StageFourRow(
                rowNumber=group_index,
                recordId=record_label,
                cells=group_data.cells,
                sourceRowNumber=group_data.source_row_number,
            ),
        )
    return output


def _summarize_record_ids(record_ids: list[str]) -> str:
    """why: provide a compact label for grouped rows."""

    filtered = [record_id for record_id in record_ids if record_id]
    if not filtered:
        return "Multiple records"
    if len(filtered) == 1:
        return filtered[0]
    return f"{filtered[0]} + {len(filtered) - 1} more"


__all__ = ["stage_four_router", "STAGE_FOUR_STATIC_PATH"]
