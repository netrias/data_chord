"""
HTTP routes for reviewing harmonized results and applying manual overrides.

Maps review HTTP requests onto Stage 4 use cases and lightweight lookup endpoints.
"""

from __future__ import annotations

from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from netrias_client import read_tabular
from pydantic import BaseModel, Field

import src.domain.dependencies as dependencies
from src.domain.manifest import (
    ManifestSummary,
    get_latest_override_value,
    read_manifest_parquet,
)
from src.domain.pv_persistence import column_pv_sets
from src.domain.pv_validation import check_value_conformance
from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN
from src.domain.storage import FileType, UploadStorage
from src.stage_4_review_results.schemas import (
    DeleteOverridesResponse,
    NonConformantItem,
    NonConformantResponse,
    ReviewOverridesSchema,
    RowContextRequest,
    RowContextResponse,
    SaveOverridesRequest,
    SaveOverridesResponse,
    StageFourResultsResponse,
)
from src.stage_4_review_results.use_cases import (
    StageFourRowsManifestNotFoundError,
    StageFourRowsUploadNotFoundError,
    build_stage_four_rows,
    save_review_overrides,
)

_MODULE_DIR = FilePath(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"


_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


class StageFourResultsRequest(BaseModel):
    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)


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
    storage: UploadStorage = dependencies.get_upload_storage()
    try:
        return build_stage_four_rows(file_id=payload.file_id, upload_storage=storage)
    except StageFourRowsUploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Upload not found. Please rerun harmonization.") from exc
    except StageFourRowsManifestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Harmonization manifest not found. Please rerun Stage 3.") from exc


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
    store = dependencies.get_file_store()
    saved = store.load_review_overrides(file_id)
    if saved is None:
        return None
    return ReviewOverridesSchema.model_validate(saved.to_store())


@stage_four_router.post("/overrides", response_model=SaveOverridesResponse, name="stage_four_save_overrides")
async def save_overrides(payload: SaveOverridesRequest) -> SaveOverridesResponse:
    store = dependencies.get_file_store()
    storage = dependencies.get_upload_storage()
    result = save_review_overrides(
        file_store=store,
        upload_storage=storage,
        file_id=payload.file_id,
        overrides=payload.overrides,
        review_state=payload.review_state,
    )
    return SaveOverridesResponse(file_id=result.file_id, updated_at=result.updated_at)


@stage_four_router.delete(
    "/overrides/{file_id}",
    response_model=DeleteOverridesResponse,
    name="stage_four_delete_overrides",
)
async def delete_overrides(file_id: FileIdPath) -> DeleteOverridesResponse:
    store = dependencies.get_file_store()
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
    storage = dependencies.get_upload_storage()
    manifest = _load_manifest(storage, file_id)

    if manifest is None:
        return NonConformantResponse(count=0, items=[])

    column_pv_map = column_pv_sets(file_id, [row.column_key for row in manifest.rows])

    # Track unique (column, original, final) tuples to avoid counting duplicates
    seen: set[tuple[str, str, str]] = set()
    non_conformant: list[NonConformantItem] = []

    for row in manifest.rows:
        # Get the current value (latest override > AI harmonization)
        latest_override = get_latest_override_value(row.manual_overrides)
        current_value = latest_override if latest_override is not None else row.top_harmonization

        # Skip if we've already processed this exact mapping
        col_key = str(row.column_key)
        key = (col_key, row.to_harmonize, current_value or "")
        if key in seen:
            continue
        seen.add(key)

        # Check PV conformance using shared function for consistent behavior
        pv_set = column_pv_map.get(col_key)
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
    storage = dependencies.get_upload_storage()
    meta = storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found")

    dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)

    selected_rows: list[list[str]] = []
    for idx in payload.row_indices:
        if 0 <= idx < len(dataset.rows):
            selected_rows.append(dataset.rows[idx])

    return RowContextResponse(headers=dataset.headers, rows=selected_rows)


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
    storage = dependencies.get_upload_storage()
    manifest = _load_manifest(storage, payload.file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Manifest not found")

    for row in manifest.rows:
        if str(row.column_key) == payload.column_key and row.to_harmonize == payload.original_value:
            return TermRowIndicesResponse(row_indices=row.row_indices)

    return TermRowIndicesResponse(row_indices=[])
