"""Serve Stage 5 results reflection routes."""

from __future__ import annotations

from collections import defaultdict
from csv import DictReader
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.stage_1_upload.dependencies import get_upload_storage
from src.stage_1_upload.services import UploadStorage

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_FIVE_STATIC_PATH = MODULE_DIR / "static"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

stage_five_router = APIRouter(prefix="/stage-5", tags=["Stage 5 Review Results"])


class StageFiveSummaryRequest(BaseModel):
    file_id: str
    manual_columns: list[str] = []


class ColumnSummary(BaseModel):
    column: str
    ai_changes: int
    manual_changes: int
    unchanged: int


class ChangeExample(BaseModel):
    row_index: int
    column: str
    original: str | None
    harmonized: str | None


class StageFiveSummaryResponse(BaseModel):
    total_rows: int
    columns_reviewed: int
    ai_changes: int
    manual_changes: int
    column_summaries: list[ColumnSummary]
    ai_examples: list[ChangeExample]
    manual_examples: list[ChangeExample]


@stage_five_router.get("", response_class=HTMLResponse, name="stage_five_review_page")
async def render_stage_five(request: Request) -> HTMLResponse:
    """why: serve the harmonization results reflection UI."""

    context = {
        "request": request,
        "stage_one_url": request.url_for("stage_one_upload_page"),
        "stage_two_url": request.url_for("stage_two_mapping_page"),
        "stage_three_url": request.url_for("stage_three_entry"),
        "stage_four_url": request.url_for("stage_four_review_page"),
        "stage_three_payload_key": "stage3HarmonizePayload",
        "summary_endpoint": request.url_for("stage_five_summary"),
    }
    return _templates.TemplateResponse("stage_5_review.html", context)


@stage_five_router.post("/summary", response_model=StageFiveSummaryResponse, name="stage_five_summary")
async def summarize_harmonized_results(payload: StageFiveSummaryRequest) -> StageFiveSummaryResponse:
    storage: UploadStorage = get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.")
    harmonized_path = _resolve_harmonized_path(meta.saved_path, payload.file_id)
    headers, original_rows = _load_csv(meta.saved_path)
    harmonized_headers, harmonized_rows = _load_csv(harmonized_path)
    effective_headers = harmonized_headers or headers
    if not effective_headers:
        raise HTTPException(status_code=400, detail="Unable to read harmonized dataset headers.")

    return _summarize_differences(
        effective_headers,
        original_rows,
        harmonized_rows,
        payload.manual_columns,
    )


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
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset missing: {path.name}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = DictReader(handle)
        rows = [row for row in reader]
        headers = reader.fieldnames or []
    return headers, rows


def _summarize_differences(
    headers: Iterable[str],
    original_rows: list[dict[str, str]],
    harmonized_rows: list[dict[str, str]],
    manual_columns: list[str],
) -> StageFiveSummaryResponse:
    header_list = list(headers)
    total_rows = min(len(original_rows), len(harmonized_rows))
    manual_set = {column.strip().lower() for column in manual_columns if column}
    stats = {column: defaultdict(int) for column in header_list}
    ai_examples: list[ChangeExample] = []
    manual_examples: list[ChangeExample] = []

    for idx in range(total_rows):
        original_row = original_rows[idx]
        harmonized_row = harmonized_rows[idx]
        for column in header_list:
            original_value = (original_row.get(column) or "").strip()
            harmonized_value = (harmonized_row.get(column) or "").strip()
            if original_value == harmonized_value:
                stats[column]["unchanged"] += 1
                continue
            if column.lower() in manual_set:
                stats[column]["manual"] += 1
                if len(manual_examples) < 20:
                    manual_examples.append(
                        ChangeExample(
                            row_index=idx + 1,
                            column=column,
                            original=original_value or None,
                            harmonized=harmonized_value or None,
                        ),
                    )
            else:
                stats[column]["ai"] += 1
                if len(ai_examples) < 20:
                    ai_examples.append(
                        ChangeExample(
                            row_index=idx + 1,
                            column=column,
                            original=original_value or None,
                            harmonized=harmonized_value or None,
                        ),
                    )

    column_summaries = [
        ColumnSummary(
            column=column,
            ai_changes=stats[column]["ai"],
            manual_changes=stats[column]["manual"],
            unchanged=stats[column]["unchanged"],
        )
        for column in header_list
    ]

    ai_changes = sum(summary.ai_changes for summary in column_summaries)
    manual_changes = sum(summary.manual_changes for summary in column_summaries)

    return StageFiveSummaryResponse(
        total_rows=total_rows,
        columns_reviewed=len(header_list),
        ai_changes=ai_changes,
        manual_changes=manual_changes,
        column_summaries=column_summaries,
        ai_examples=ai_examples,
        manual_examples=manual_examples,
    )


__all__ = ["stage_five_router", "STAGE_FIVE_STATIC_PATH"]
