"""Stage 4 review use cases for building rows and saving override state."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from netrias_client import read_tabular

from src.domain import CONFIDENCE, RecommendationType
from src.domain.columns import ColumnIdentity
from src.domain.manifest import (
    ConfidenceBucket,
    ManifestRow,
    ManifestSummary,
    add_manual_overrides_batch,
    confidence_bucket,
    get_latest_override_value,
    is_value_changed,
    read_manifest_parquet,
)
from src.domain.pv_persistence import column_pv_sets
from src.domain.pv_validation import check_value_conformance
from src.domain.review_overrides import ReviewOverrides
from src.domain.storage import FileStore, UploadStorage
from src.stage_4_review_results.schemas import (
    CellOverrideSchema,
    ColumnReviewData,
    ReviewStateSchema,
    StageFourResultsResponse,
    SuggestionInfo,
    Transformation,
)

logger = logging.getLogger(__name__)

ReviewOverridePayload = Mapping[str, Mapping[str, CellOverrideSchema]]


@dataclass(frozen=True)
class SaveReviewOverridesResult:
    file_id: str
    updated_at: datetime


class StageFourRowsUploadNotFoundError(Exception):
    """Raised when Stage 4 rows are requested for an unknown upload."""


class StageFourRowsManifestNotFoundError(Exception):
    """Raised when Stage 4 rows are requested before Stage 3 stores a manifest."""


def build_stage_four_rows(*, file_id: str, upload_storage: UploadStorage) -> StageFourResultsResponse:
    meta = upload_storage.load(file_id)
    if not meta:
        raise StageFourRowsUploadNotFoundError()

    original_dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)

    manifest = _load_manifest(upload_storage, file_id)
    if manifest is None:
        raise StageFourRowsManifestNotFoundError()

    column_info = _extract_columns_from_manifest(manifest)
    column_pv_map = column_pv_sets(file_id, [col.key for col in column_info])
    column_pvs = _build_column_pvs(column_info, column_pv_map, file_id)
    columns = _build_columns_from_manifest(manifest, column_pv_map)

    return StageFourResultsResponse(
        columns=columns,
        columnPVs=column_pvs,
        totalOriginalRows=len(original_dataset.rows),
    )


def save_review_overrides(
    *,
    file_store: FileStore,
    upload_storage: UploadStorage,
    file_id: str,
    overrides: ReviewOverridePayload,
    review_state: ReviewStateSchema,
) -> SaveReviewOverridesResult:
    """Persist export overrides and append the matching manifest audit rows."""
    now = datetime.now(UTC)
    existing = file_store.load_review_overrides(file_id)
    saved = ReviewOverrides.create(
        file_id=file_id,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        overrides=_override_payload_to_store(overrides),
        review_state=review_state.model_dump(),
    )
    file_store.save_review_overrides(saved)
    _sync_override_audit(upload_storage, saved)
    return SaveReviewOverridesResult(file_id=file_id, updated_at=now)


def _load_manifest(storage: UploadStorage, file_id: str) -> ManifestSummary | None:
    manifest_path = storage.load_harmonization_manifest_path(file_id)
    if manifest_path is None:
        return None
    return read_manifest_parquet(manifest_path)


def _extract_columns_from_manifest(manifest: ManifestSummary) -> list[ColumnIdentity]:
    seen: set[str] = set()
    columns: list[ColumnIdentity] = []
    for row in manifest.rows:
        col_key = str(row.column_key)
        if col_key not in seen:
            seen.add(col_key)
            columns.append(ColumnIdentity(key=row.column_key, index=row.column_id, header=row.column_name))
    return columns


def _build_columns_from_manifest(
    manifest: ManifestSummary,
    column_pv_map: dict[str, frozenset[str] | None],
) -> list[ColumnReviewData]:
    columns_map: dict[str, list[ManifestRow]] = {}
    column_indices: dict[str, int] = {}
    column_labels: dict[str, str] = {}

    for row in manifest.rows:
        col_key = str(row.column_key)
        if col_key not in columns_map:
            columns_map[col_key] = []
            column_indices[col_key] = row.column_id
            column_labels[col_key] = row.column_name
        columns_map[col_key].append(row)

    columns: list[ColumnReviewData] = []
    for col_key in sorted(columns_map.keys(), key=lambda c: column_indices[c]):
        manifest_rows = columns_map[col_key]
        transformations = [
            _build_transformation(row, column_pv_map.get(col_key)) for row in manifest_rows
        ]
        terms_with_changes = sum(1 for transformation in transformations if transformation.isChanged)

        columns.append(ColumnReviewData(
            columnKey=col_key,
            columnLabel=column_labels[col_key] or "Unknown",
            sourceColumnIndex=column_indices[col_key],
            termCount=len(transformations),
            termsWithChanges=terms_with_changes,
            transformations=transformations,
        ))

    return columns


def _build_transformation(row: ManifestRow, pv_set: frozenset[str] | None) -> Transformation:
    original_value = row.to_harmonize or ""
    harmonized_value = row.top_harmonization or None
    confidence = row.confidence_score
    is_changed = is_value_changed(original_value, harmonized_value)
    recommendation_type = _compute_recommendation_type(original_value, harmonized_value)

    if confidence is not None:
        bucket = confidence_bucket(confidence)
    else:
        bucket = ConfidenceBucket.LOW if is_changed else ConfidenceBucket.HIGH
        confidence = CONFIDENCE.HIGH if bucket == ConfidenceBucket.HIGH else CONFIDENCE.LOW

    manual_override = get_latest_override_value(row.manual_overrides)
    current_value = manual_override if manual_override is not None else harmonized_value
    manifest_indices_full = [idx + 1 for idx in row.row_indices]
    row_count = len(manifest_indices_full)

    return Transformation(
        originalValue=original_value,
        harmonizedValue=harmonized_value,
        bucket=bucket.value,
        confidence=confidence,
        isChanged=is_changed,
        recommendationType=recommendation_type.value,
        manualOverride=manual_override,
        isPVConformant=check_value_conformance(current_value, pv_set),
        pvSetAvailable=pv_set is not None and len(pv_set) > 0,
        topSuggestions=_build_suggestions_with_conformance(row.top_harmonizations, pv_set),
        rowIndices=manifest_indices_full if row_count <= 50 else manifest_indices_full[:10],
        rowCount=row_count,
    )


def _build_column_pvs(
    columns: list[ColumnIdentity],
    column_pv_map: dict[str, frozenset[str] | None],
    file_id: str,
) -> dict[str, list[str]]:
    """Alphabetical sort ensures predictable dropdown ordering across page loads."""
    column_pvs: dict[str, list[str]] = {}
    columns_without_pvs: list[str] = []

    for col_info in columns:
        pv_set = column_pv_map.get(str(col_info.key))
        if pv_set:
            column_pvs[str(col_info.key)] = sorted(pv_set)
        else:
            columns_without_pvs.append(col_info.header)

    pv_summary = {k: len(v) for k, v in column_pvs.items()}
    logger.info(
        "Built column PVs",
        extra={
            "file_id": file_id,
            "columns_with_pvs": len(column_pvs),
            "columns_without_pvs": columns_without_pvs[:5] if columns_without_pvs else [],
            "pv_counts": pv_summary,
        },
    )

    if not column_pvs and columns:
        logger.warning(
            "No PVs available for any column. PV combobox will not appear in Stage 4.",
            extra={"file_id": file_id, "column_count": len(columns)},
        )

    return column_pvs


def _build_suggestions_with_conformance(
    suggestions: list[str],
    pv_set: frozenset[str] | None,
) -> list[SuggestionInfo]:
    return [
        SuggestionInfo(value=suggestion, isPVConformant=check_value_conformance(suggestion, pv_set))
        for suggestion in suggestions
    ]


def _compute_recommendation_type(
    original_value: str | None,
    harmonized_value: str | None,
) -> RecommendationType:
    if not harmonized_value or not harmonized_value.strip():
        return RecommendationType.NO_RECOMMENDATION

    if (original_value or "") != harmonized_value:
        return RecommendationType.AI_CHANGED

    return RecommendationType.AI_UNCHANGED


def _override_payload_to_store(
    overrides: ReviewOverridePayload,
) -> dict[str, dict[str, dict[str, str | None]]]:
    return {
        row_key: {
            column_key: {
                "ai_value": override.ai_value,
                "human_value": override.human_value,
                "original_value": override.original_value,
            }
            for column_key, override in columns.items()
        }
        for row_key, columns in overrides.items()
    }


def _sync_override_audit(storage: UploadStorage, overrides: ReviewOverrides) -> None:
    manifest_path = storage.load_harmonization_manifest_path(overrides.file_id)
    if manifest_path is None:
        logger.warning("Cannot sync overrides: manifest path not found", extra={"file_id": overrides.file_id})
        return

    overrides_batch = overrides.manual_override_batch()
    if not overrides_batch:
        return

    success = add_manual_overrides_batch(
        manifest_path=manifest_path,
        overrides=overrides_batch,
        user_id=None,
    )
    if not success:
        logger.error("Failed to sync overrides to manifest parquet", extra={"file_id": overrides.file_id})


__all__ = [
    "SaveReviewOverridesResult",
    "StageFourRowsManifestNotFoundError",
    "StageFourRowsUploadNotFoundError",
    "build_stage_four_rows",
    "save_review_overrides",
]
