"""Stage 4 review use cases for building rows and saving override state."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from netrias_client import read_tabular

from src.domain import CONFIDENCE, RecommendationType
from src.domain.columns import ColumnIdentity, ColumnKey
from src.domain.dataset_workflow_ids import DatasetWorkflowId
from src.domain.manifest import (
    ConfidenceBucket,
    ManifestRow,
    ManifestSummary,
    confidence_bucket,
    get_latest_override_value,
    is_value_changed,
)
from src.domain.pv_validation import check_value_conformance
from src.domain.review_overrides import ReviewOverrides, ReviewProgressState
from src.persistence.cde_mapping_document_store import CdeMappingEntry, load_cde_mapping_entries_by_column
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_writer import add_manual_overrides_batch
from src.persistence.pv_manifest_store import ColumnPvSets, column_pv_sets
from src.persistence.review_override_store import (
    delete_review_overrides_state,
    load_review_overrides,
    save_review_overrides_state,
)
from src.persistence.workflow_artifacts import (
    load_harmonization_manifest_path,
    load_upload_artifact,
)
from src.stage_4_review_results.schemas import (
    CellOverrideSchema,
    ColumnReviewData,
    DeleteOverridesResponse,
    NonConformantItem,
    NonConformantResponse,
    ReviewOverridesSchema,
    ReviewStateSchema,
    RowContextResponse,
    StageFourResultsResponse,
    SuggestionInfo,
    TermRowIndicesResponse,
    Transformation,
)
from src.storage import UploadStorage, UserContext, WorkflowFile, WorkflowStorage

logger = logging.getLogger(__name__)

ReviewOverridePayload = Mapping[str, Mapping[str, CellOverrideSchema]]


@dataclass(frozen=True)
class SaveReviewOverridesResult:
    file_id: DatasetWorkflowId
    updated_at: datetime


class StageFourRowsUploadNotFoundError(Exception):
    """Raised when Stage 4 rows are requested for an unknown upload."""


class StageFourRowsManifestNotFoundError(Exception):
    """Raised when Stage 4 rows are requested before Stage 3 stores a manifest."""


class RowContextUploadNotFoundError(Exception):
    """Raised when row context is requested for an unknown upload."""


class TermRowIndicesManifestNotFoundError(Exception):
    """Raised when term row indices are requested before Stage 3 stores a manifest."""


def build_stage_four_rows(
    *,
    file_id: DatasetWorkflowId,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> StageFourResultsResponse:
    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    if not meta:
        raise StageFourRowsUploadNotFoundError()

    original_dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)

    manifest = _load_manifest(upload_storage, workflow_storage, user, file_id)
    if manifest is None:
        raise StageFourRowsManifestNotFoundError()

    column_info = _extract_columns_from_manifest(manifest)
    column_pv_map = column_pv_sets(file_id, [col.key for col in column_info])
    column_pvs = _build_column_pvs(column_info, column_pv_map, file_id)
    cde_mappings_by_column = load_cde_mapping_entries_by_column(file_id, workflow_storage, user)
    columns = _build_columns_from_manifest(manifest, column_pv_map, cde_mappings_by_column)

    return StageFourResultsResponse(
        columns=columns,
        columnPVs=column_pvs,
        totalOriginalRows=len(original_dataset.rows),
    )


def build_non_conformant_values(
    *,
    file_id: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> NonConformantResponse:
    """Build the Stage 4 gating list from the current manifest values and durable PV state."""
    manifest = _load_manifest(upload_storage, workflow_storage, user, file_id)
    if manifest is None:
        return NonConformantResponse(count=0, items=[])

    column_pv_map = column_pv_sets(file_id, [row.column_key for row in manifest.rows])
    non_conformant = _find_unique_non_conformant_values(manifest, column_pv_map)
    return NonConformantResponse(count=len(non_conformant), items=non_conformant)


def build_row_context(
    *,
    file_id: str,
    row_indices: list[int],
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> RowContextResponse:
    """Load original spreadsheet rows for the on-demand review context popup."""
    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    if meta is None:
        raise RowContextUploadNotFoundError()

    dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)
    selected_rows = [
        dataset.rows[index]
        for index in row_indices
        if 0 <= index < len(dataset.rows)
    ]
    return RowContextResponse(headers=dataset.headers, rows=selected_rows)


def find_term_row_indices(
    *,
    file_id: str,
    column_key: str,
    original_value: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> TermRowIndicesResponse:
    """Look up full 0-based source row indices for a manifest term."""
    manifest = _load_manifest(upload_storage, workflow_storage, user, file_id)
    if manifest is None:
        raise TermRowIndicesManifestNotFoundError()

    for row in manifest.rows:
        if str(row.column_key) == column_key and row.to_harmonize == original_value:
            return TermRowIndicesResponse(row_indices=row.row_indices)

    return TermRowIndicesResponse(row_indices=[])


def get_review_overrides(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId,
) -> ReviewOverridesSchema | None:
    saved = load_review_overrides(workflow_storage, user, file_id)
    if saved is None:
        return None
    return ReviewOverridesSchema.model_validate(saved.to_store())


def delete_review_overrides(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId,
) -> DeleteOverridesResponse:
    existed = delete_review_overrides_state(workflow_storage, user, file_id)
    return DeleteOverridesResponse(file_id=file_id, deleted=existed)


def save_review_overrides(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    upload_storage: UploadStorage,
    file_id: DatasetWorkflowId,
    overrides: ReviewOverridePayload,
    review_state: ReviewStateSchema,
) -> SaveReviewOverridesResult:
    """Persist export overrides and append the matching manifest audit rows."""
    saved = save_review_overrides_state(
        workflow_storage,
        user,
        file_id=file_id,
        overrides=_override_payload_to_store(overrides),
        review_state=ReviewProgressState.from_payload(review_state.model_dump(mode="json")),
    )
    _sync_override_audit(upload_storage, workflow_storage, user, saved)
    return SaveReviewOverridesResult(file_id=file_id, updated_at=saved.updated_at)


def _load_manifest(
    storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> ManifestSummary | None:
    manifest_path = load_harmonization_manifest_path(storage, workflow_storage, user, file_id)
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


def _find_unique_non_conformant_values(
    manifest: ManifestSummary,
    column_pv_map: ColumnPvSets,
) -> list[NonConformantItem]:
    seen: set[tuple[str, str, str]] = set()
    non_conformant: list[NonConformantItem] = []

    for row in manifest.rows:
        current_value = _current_value_for_row(row)
        col_key = str(row.column_key)
        # Gate once per unique term/current value pair; repeated source rows
        # should not make reviewers resolve the same problem more than once.
        key = (col_key, row.to_harmonize, current_value or "")
        if key in seen:
            continue
        seen.add(key)

        pv_set = column_pv_map.get(row.column_key)
        if pv_set and current_value and not check_value_conformance(current_value, pv_set):
            non_conformant.append(NonConformantItem(
                column=row.column_name,
                value=current_value,
                original=row.to_harmonize,
            ))

    return non_conformant


def _current_value_for_row(row: ManifestRow) -> str:
    latest_override = get_latest_override_value(row.manual_overrides)
    return latest_override if latest_override is not None else row.top_harmonization


def _build_columns_from_manifest(
    manifest: ManifestSummary,
    column_pv_map: ColumnPvSets,
    cde_mappings_by_column: Mapping[ColumnKey, CdeMappingEntry],
) -> list[ColumnReviewData]:
    columns_map: dict[ColumnKey, list[ManifestRow]] = {}
    column_indices: dict[ColumnKey, int] = {}
    column_labels: dict[ColumnKey, str] = {}

    for row in manifest.rows:
        col_key = row.column_key
        if col_key not in columns_map:
            columns_map[col_key] = []
            column_indices[col_key] = row.column_id
            column_labels[col_key] = row.column_name
        columns_map[col_key].append(row)

    columns: list[ColumnReviewData] = []
    for col_key in sorted(columns_map.keys(), key=lambda c: column_indices[c]):
        manifest_rows = columns_map[col_key]
        mapping_entry = cde_mappings_by_column.get(col_key)
        target_cde_key = _target_cde_key(mapping_entry)
        serialized_col_key = str(col_key)
        transformations = [
            _build_transformation(row, column_pv_map.get(row.column_key)) for row in manifest_rows
        ]
        terms_with_changes = sum(1 for transformation in transformations if transformation.isChanged)

        columns.append(ColumnReviewData(
            columnKey=serialized_col_key,
            columnLabel=column_labels[col_key] or "Unknown",
            targetCdeKey=target_cde_key,
            targetCdeLabel=target_cde_key,
            sourceColumnIndex=column_indices[col_key],
            termCount=len(transformations),
            termsWithChanges=terms_with_changes,
            transformations=transformations,
        ))

    return columns


def _target_cde_key(mapping_entry: CdeMappingEntry | None) -> str | None:
    if mapping_entry is None:
        return None
    return mapping_entry.cde_key


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
    column_pv_map: ColumnPvSets,
    file_id: str,
) -> dict[str, list[str]]:
    """Alphabetical sort ensures predictable dropdown ordering across page loads."""
    column_pvs: dict[str, list[str]] = {}
    columns_without_pvs: list[str] = []

    for col_info in columns:
        pv_set = column_pv_map.get(col_info.key)
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


def _sync_override_audit(
    storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    overrides: ReviewOverrides,
) -> None:
    manifest_path = load_harmonization_manifest_path(storage, workflow_storage, user, overrides.file_id)
    if manifest_path is None:
        logger.warning("Cannot sync overrides: manifest path not found", extra={"file_id": overrides.file_id})
        return

    overrides_batch = overrides.manual_override_batch()
    if not overrides_batch:
        return

    # The parquet manifest is the Stage 5 audit source, so mirror saved review
    # edits there instead of treating the override JSON as a separate truth.
    success = add_manual_overrides_batch(
        manifest_path=manifest_path,
        overrides=overrides_batch,
        user_id=None,
    )
    if not success:
        logger.error("Failed to sync overrides to manifest parquet", extra={"file_id": overrides.file_id})
        return

    workflow_storage.write_artifact(user, overrides.file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE, manifest_path)


__all__ = [
    "SaveReviewOverridesResult",
    "RowContextUploadNotFoundError",
    "StageFourRowsManifestNotFoundError",
    "StageFourRowsUploadNotFoundError",
    "TermRowIndicesManifestNotFoundError",
    "build_non_conformant_values",
    "build_row_context",
    "build_stage_four_rows",
    "delete_review_overrides",
    "find_term_row_indices",
    "get_review_overrides",
    "save_review_overrides",
]
