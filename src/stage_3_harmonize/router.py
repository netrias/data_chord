"""Serve Stage 3 harmonization routes and assets."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.domain import (
    DEFAULT_TARGET_SCHEMA,
    ColumnBreakdownSchema,
    ColumnMappingSet,
    ConfidenceBucketSchema,
    HarmonizeRequest,
    HarmonizeResponse,
    ManifestSummarySchema,
)
from src.domain.dependencies import get_harmonize_service, get_upload_storage
from src.domain.manifest import (
    ConfidenceBucket,
    ManifestPayload,
    ManifestRow,
    ManifestSummary,
    confidence_bucket,
    is_value_changed,
    read_manifest_parquet,
)

MODULE_DIR = Path(__file__).parent
TEMPLATE_DIR = MODULE_DIR / "templates"
STAGE_THREE_STATIC_PATH = MODULE_DIR / "static"
NEXT_STAGE_PATH = "/stage-4"

_templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
_storage = get_upload_storage()
_router_logger = logging.getLogger(__name__)

stage_three_router = APIRouter(prefix="/stage-3", tags=["Stage 3 Harmonize"])


@stage_three_router.get("", response_class=HTMLResponse, name="stage_three_entry")
async def render_stage_three(request: Request) -> HTMLResponse:
    """why: serve the harmonization progress UI."""

    context = {
        "request": request,
        "default_schema": DEFAULT_TARGET_SCHEMA,
        "next_stage_url": NEXT_STAGE_PATH,
    }
    return _templates.TemplateResponse("stage_3_harmonize.html", context)


@stage_three_router.post(
    "/harmonize",
    response_model=HarmonizeResponse,
    name="stage_three_harmonize",
)
async def harmonize_dataset(payload: HarmonizeRequest) -> HarmonizeResponse:
    """why: trigger harmonization using the Netrias client."""

    meta = _storage.load(payload.file_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found. Please rerun analysis.")

    stored_manifest = _storage.load_manifest(payload.file_id)
    manifest_payload = payload.manifest or cast(ManifestPayload | None, stored_manifest)
    column_mappings = ColumnMappingSet.from_dict(payload.manual_overrides)
    harmonizer = get_harmonize_service()
    result = await run_in_threadpool(
        harmonizer.run,
        file_path=meta.saved_path,
        target_schema=payload.target_schema,
        column_mappings=column_mappings,
        manifest=manifest_payload,
    )
    _router_logger.info(
        "Harmonization job dispatched",
        extra={"file_id": payload.file_id, "job_id": result.job_id, "status": result.status},
    )

    manifest_summary = _read_and_store_manifest(payload.file_id, result.manifest_path)

    query_params = urlencode({
        "file_id": payload.file_id,
        "job_id": result.job_id,
        "status": result.status,
        "detail": result.detail or "",
    })
    next_stage_url = f"{NEXT_STAGE_PATH}?{query_params}"
    return HarmonizeResponse(
        job_id=result.job_id,
        status=result.status,
        detail=result.detail,
        next_stage_url=next_stage_url,
        job_id_available=result.job_id_available,
        manifest_summary=manifest_summary,
    )


def _read_and_store_manifest(file_id: str, manifest_path: Path | None) -> ManifestSummarySchema | None:
    """why: read manifest parquet and store for later stages."""
    if manifest_path is None or not manifest_path.exists():
        return None

    manifest_data = read_manifest_parquet(manifest_path)
    if manifest_data is None:
        return None

    _ = _storage.save_harmonization_manifest(file_id, manifest_path)
    return _convert_to_schema(manifest_data)


def _format_column_label(column_name: str) -> str:
    """why: convert snake_case column names to readable labels."""
    if not column_name:
        return "Unknown"
    return column_name.replace("_", " ").title()


def _build_column_breakdowns(rows: list[ManifestRow]) -> list[ColumnBreakdownSchema]:
    """why: aggregate per-column statistics from manifest rows."""
    column_rows: dict[str, list[ManifestRow]] = defaultdict(list)
    for row in rows:
        column_rows[row.column_name].append(row)

    breakdowns: list[ColumnBreakdownSchema] = []
    for column_name, col_rows in column_rows.items():
        total_rows = 0
        changed_rows = 0
        unique_terms_changed = 0
        confidence_counts_changed: dict[ConfidenceBucket, int] = {
            ConfidenceBucket.HIGH: 0,
            ConfidenceBucket.MEDIUM: 0,
            ConfidenceBucket.LOW: 0,
        }

        for row in col_rows:
            row_count = len(row.row_indices) if row.row_indices else 1
            total_rows += row_count

            if is_value_changed(row.to_harmonize, row.top_harmonization):
                changed_rows += row_count
                unique_terms_changed += 1
                bucket = confidence_bucket(row.confidence_score)
                confidence_counts_changed[bucket] += 1

        unique_terms = len(col_rows)
        breakdowns.append(ColumnBreakdownSchema(
            column_name=column_name,
            label=_format_column_label(column_name),
            total_rows=total_rows,
            changed_rows=changed_rows,
            unchanged_rows=total_rows - changed_rows,
            unique_terms=unique_terms,
            unique_terms_changed=unique_terms_changed,
            unique_terms_unchanged=unique_terms - unique_terms_changed,
            confidence_buckets_changed=[
                ConfidenceBucketSchema(
                    id=bucket.value,
                    label=bucket.label,
                    term_count=confidence_counts_changed[bucket],
                )
                for bucket in ConfidenceBucket
            ],
        ))

    breakdowns.sort(key=lambda b: (b.changed_rows == 0, -b.total_rows))
    return breakdowns


def _convert_to_schema(manifest: ManifestSummary) -> ManifestSummarySchema:
    """why: transform internal manifest data into API response schema."""
    column_breakdowns = _build_column_breakdowns(manifest.rows)
    return ManifestSummarySchema(
        total_terms=manifest.total_terms,
        changed_terms=manifest.changed_terms,
        high_confidence_count=manifest.high_confidence_count,
        medium_confidence_count=manifest.medium_confidence_count,
        low_confidence_count=manifest.low_confidence_count,
        column_breakdowns=column_breakdowns,
    )


__all__ = ["stage_three_router", "STAGE_THREE_STATIC_PATH"]
