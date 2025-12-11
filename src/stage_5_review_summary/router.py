"""
Serve the Stage 5 harmonization summary UI and compute change metrics.

Compare original vs harmonized CSVs to show AI and manual change statistics.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from csv import DictReader
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.domain import ChangeType, SessionKey
from src.stage_1_upload.dependencies import get_upload_storage
from src.stage_1_upload.manifest_reader import ManifestSummary, read_manifest_parquet
from src.stage_1_upload.services import UploadStorage

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_FIVE_STATIC_PATH = MODULE_DIR / "static"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

stage_five_router = APIRouter(prefix="/stage-5", tags=["Stage 5 Review Results"])


class StageFiveSummaryRequest(BaseModel):
    """Identify which file to summarize and which columns had manual edits."""

    file_id: str
    manual_columns: list[str] = []


class ColumnSummary(BaseModel):
    """Per-column breakdown of AI vs manual vs unchanged cell counts."""

    column: str
    ai_changes: int
    manual_changes: int
    unchanged: int


class ChangeExample(BaseModel):
    """Single cell change with before/after values for display."""

    row_index: int
    column: str
    original: str | None
    harmonized: str | None


class StageFiveSummaryResponse(BaseModel):
    """Aggregate change statistics returned to the Stage 5 UI."""

    total_rows: int
    columns_reviewed: int
    ai_changes: int
    manual_changes: int
    column_summaries: list[ColumnSummary]
    ai_examples: list[ChangeExample]
    manual_examples: list[ChangeExample]
    high_confidence_changes: int = 0
    medium_confidence_changes: int = 0
    low_confidence_changes: int = 0


@stage_five_router.get("", response_class=HTMLResponse, name="stage_five_review_page")
async def render_stage_five(request: Request) -> HTMLResponse:
    """why: serve the harmonization results reflection UI."""
    context = {
        "request": request,
        "stage_one_url": request.url_for("stage_one_upload_page"),
        "stage_two_url": request.url_for("stage_two_mapping_page"),
        "stage_three_url": request.url_for("stage_three_entry"),
        "stage_four_url": request.url_for("stage_four_review_page"),
        "stage_three_payload_key": SessionKey.STAGE_THREE_PAYLOAD.value,
        "summary_endpoint": request.url_for("stage_five_summary"),
    }
    return _templates.TemplateResponse("stage_5_review.html", context)


@stage_five_router.post("/summary", response_model=StageFiveSummaryResponse, name="stage_five_summary")
async def summarize_harmonized_results(payload: StageFiveSummaryRequest) -> StageFiveSummaryResponse:
    """why: load both CSVs and compute AI vs manual change statistics."""
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

    manifest = _load_manifest_summary(storage, payload.file_id)

    return _summarize_differences(
        effective_headers,
        original_rows,
        harmonized_rows,
        payload.manual_columns,
        manifest,
    )


def _resolve_harmonized_path(original_path: Path, file_id: str) -> Path:
    'Find the harmonized output file using naming conventions.'
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
    'Read CSV into headers list and row dictionaries.'
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset missing: {path.name}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = DictReader(handle)
        rows = list(reader)
        headers = list(reader.fieldnames) if reader.fieldnames else []
    return headers, rows


def _summarize_differences(
    headers: Iterable[str],
    original_rows: list[dict[str, str]],
    harmonized_rows: list[dict[str, str]],
    manual_columns: list[str],
    manifest: ManifestSummary | None,
) -> StageFiveSummaryResponse:
    """why: walk rows cell-by-cell to tally changes and collect examples."""
    header_list = list(headers)
    total_rows = min(len(original_rows), len(harmonized_rows))
    manual_set = {column.strip().lower() for column in manual_columns if column}
    stats: dict[str, defaultdict[str, int]] = {column: defaultdict(int) for column in header_list}
    ai_examples: list[ChangeExample] = []
    manual_examples: list[ChangeExample] = []

    for idx in range(total_rows):
        original_row = original_rows[idx]
        harmonized_row = harmonized_rows[idx]
        for column in header_list:
            original_value = (original_row.get(column) or "").strip()
            harmonized_value = (harmonized_row.get(column) or "").strip()
            if original_value == harmonized_value:
                stats[column][ChangeType.UNCHANGED.value] += 1
                continue
            if column.lower() in manual_set:
                stats[column][ChangeType.MANUAL_OVERRIDE.value] += 1
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
                stats[column][ChangeType.AI_HARMONIZED.value] += 1
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
            ai_changes=stats[column][ChangeType.AI_HARMONIZED.value],
            manual_changes=stats[column][ChangeType.MANUAL_OVERRIDE.value],
            unchanged=stats[column][ChangeType.UNCHANGED.value],
        )
        for column in header_list
    ]

    ai_changes = sum(summary.ai_changes for summary in column_summaries)
    manual_changes = sum(summary.manual_changes for summary in column_summaries)

    high_confidence, medium_confidence, low_confidence = _extract_confidence_counts(manifest)

    return StageFiveSummaryResponse(
        total_rows=total_rows,
        columns_reviewed=len(header_list),
        ai_changes=ai_changes,
        manual_changes=manual_changes,
        column_summaries=column_summaries,
        ai_examples=ai_examples,
        manual_examples=manual_examples,
        high_confidence_changes=high_confidence,
        medium_confidence_changes=medium_confidence,
        low_confidence_changes=low_confidence,
    )


def _load_manifest_summary(storage: UploadStorage, file_id: str) -> ManifestSummary | None:
    """why: load the harmonization manifest for confidence metrics."""
    manifest_path = storage.load_harmonization_manifest_path(file_id)
    if manifest_path is None:
        return None
    return read_manifest_parquet(manifest_path)


def _extract_confidence_counts(manifest: ManifestSummary | None) -> tuple[int, int, int]:
    """why: extract confidence breakdown from manifest or return zeros."""
    if manifest is None:
        return (0, 0, 0)
    return (
        manifest.high_confidence_count,
        manifest.medium_confidence_count,
        manifest.low_confidence_count,
    )
