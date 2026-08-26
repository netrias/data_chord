"""Stage 4 review use cases for building rows and saving override state."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from netrias_client import read_tabular

from src.app.harmonization_readiness import (
    REVIEW_STATE_RECOVERY_DETAIL,
    HarmonizationNotReadyError,
    capture_ready_harmonization,
    load_readable_review_overrides_record,
    require_review_state_matches_manifest,
)
from src.domain.change import RecommendationType
from src.domain.columns import ColumnIdentity, ColumnKey
from src.domain.dataset_workflow_ids import DatasetWorkflowId
from src.domain.manifest import (
    ManifestRow,
    ManifestSummary,
    is_value_changed,
)
from src.domain.pv_validation import check_value_conformance
from src.domain.review_overrides import ReviewOverrides, ReviewProgressState
from src.observability.events import performance_span
from src.persistence.cde_mapping_document_store import CdeMappingEntry, load_cde_mapping_entries_by_column
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets, column_pv_sets, effective_column_cde_map
from src.persistence.review_override_store import (
    ReviewOverridesStoreConflictError,
    ReviewOverridesUnreadableError,
    save_review_overrides_state,
)
from src.persistence.workflow_artifacts import (
    load_harmonization_manifest_path,
    load_upload_artifact,
)
from src.stage_4_review_results.schemas import (
    CellOverrideSchema,
    ColumnReviewData,
    NonConformantItem,
    NonConformantResponse,
    ReviewOverridesSchema,
    ReviewStateSchema,
    RowContextResponse,
    StageFourResultsResponse,
    SuggestionInfo,
    Transformation,
)
from src.storage import UploadStorage, UserContext, VersionToken, WorkflowStorage

logger = logging.getLogger(__name__)

ReviewOverridePayload = Mapping[str, Mapping[str, CellOverrideSchema]]


@dataclass(frozen=True)
class SaveReviewOverridesResult:
    file_id: DatasetWorkflowId
    updated_at: datetime
    version: VersionToken


@dataclass(frozen=True)
class LoadedReviewOverridesResult:
    payload: ReviewOverridesSchema
    version: VersionToken


class ReviewStateConflictError(Exception):
    """Raised when active review state changed after the caller loaded it."""


class InvalidReviewOverrideSelectionError(Exception):
    """Raised when a review snapshot refers to a cell outside the Stage 3 result."""


def build_stage_four_rows(
    *,
    file_id: DatasetWorkflowId,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> StageFourResultsResponse:
    with performance_span("stage4.rows.ready_capture"):
        ready = capture_ready_harmonization(workflow_storage, user, file_id)
    loaded_state = ready.workflow
    with performance_span("stage4.rows.upload_artifact"):
        meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    if not meta:
        raise HarmonizationNotReadyError("Upload not found. Return to Stage 1 and upload it again.")

    with performance_span("stage4.rows.source_dataset_read"):
        original_dataset = read_tabular(meta.saved_path, sheet_name=loaded_state.state.selected_sheet)

    with performance_span("stage4.rows.manifest_read"):
        manifest = _load_manifest(upload_storage, workflow_storage, user, file_id)
    if manifest is None:
        raise HarmonizationNotReadyError(
            "Harmonization results are incomplete. Return to Stage 3 and run harmonization again."
        )

    with performance_span("stage4.rows.review_state"):
        review_record = load_readable_review_overrides_record(workflow_storage, user, file_id)
        review_overrides = review_record.value if review_record is not None else None
        require_review_state_matches_manifest(review_overrides, manifest)
    column_info = _extract_columns_from_manifest(manifest)
    with performance_span("stage4.rows.pv_load"):
        column_pv_map = column_pv_sets(
            workflow_storage,
            user,
            loaded_state,
            [col.key for col in column_info],
        )
        column_pvs = _build_column_pvs(column_info, column_pv_map, file_id)
    with performance_span("stage4.rows.cde_mapping"):
        cde_mappings_by_column = load_cde_mapping_entries_by_column(file_id, workflow_storage, user)
    with performance_span("stage4.rows.response_build"):
        columns = _build_columns_from_manifest(
            manifest,
            column_pv_map,
            effective_column_cde_map(loaded_state).mappings,
            cde_mappings_by_column,
            review_overrides,
        )
        response = StageFourResultsResponse(
            columns=columns,
            columnPVs=column_pvs,
            totalOriginalRows=len(original_dataset.rows),
        )
    with performance_span("stage4.rows.ready_check"):
        ready.require_unchanged(workflow_storage, user)
    return response


def build_non_conformant_values(
    *,
    file_id: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> NonConformantResponse:
    """Build the Stage 4 gating list from the current manifest values and durable PV state."""
    with performance_span("stage4.non_conformant.ready_capture"):
        ready = capture_ready_harmonization(workflow_storage, user, file_id)
    loaded_state = ready.workflow

    with performance_span("stage4.non_conformant.manifest_read"):
        manifest = _load_manifest(upload_storage, workflow_storage, user, file_id)
    if manifest is None:
        raise HarmonizationNotReadyError(
            "Harmonization results are incomplete. Return to Stage 3 and run harmonization again."
        )

    with performance_span("stage4.non_conformant.pv_load"):
        column_pv_map = column_pv_sets(
            workflow_storage,
            user,
            loaded_state,
            [row.column_key for row in manifest.rows],
        )
    with performance_span("stage4.non_conformant.review_state"):
        review_record = load_readable_review_overrides_record(workflow_storage, user, file_id)
        review_overrides = review_record.value if review_record is not None else None
        require_review_state_matches_manifest(review_overrides, manifest)
    with performance_span("stage4.non_conformant.value_scan"):
        non_conformant = _find_unique_non_conformant_values(
            manifest,
            column_pv_map,
            review_overrides,
        )
    response = NonConformantResponse(count=len(non_conformant), items=non_conformant)
    with performance_span("stage4.non_conformant.ready_check"):
        ready.require_unchanged(workflow_storage, user)
    return response


def build_row_context(
    *,
    file_id: str,
    row_indices: list[int],
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> RowContextResponse:
    """Load original spreadsheet rows for the on-demand review context popup."""
    ready = capture_ready_harmonization(workflow_storage, user, file_id)
    loaded_state = ready.workflow
    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    if meta is None:
        raise HarmonizationNotReadyError("Upload not found. Return to Stage 1 and upload it again.")

    dataset = read_tabular(meta.saved_path, sheet_name=loaded_state.state.selected_sheet)
    selected_rows = [
        dataset.rows[index]
        for index in row_indices
        if 0 <= index < len(dataset.rows)
    ]
    response = RowContextResponse(headers=dataset.headers, rows=selected_rows)
    ready.require_unchanged(workflow_storage, user)
    return response


def get_review_overrides(
    *,
    workflow_storage: WorkflowStorage,
    upload_storage: UploadStorage,
    user: UserContext,
    file_id: DatasetWorkflowId,
) -> LoadedReviewOverridesResult | None:
    with performance_span("stage4.overrides.ready_capture"):
        ready = capture_ready_harmonization(workflow_storage, user, file_id)
    with performance_span("stage4.overrides.review_state"):
        record = load_readable_review_overrides_record(workflow_storage, user, file_id)
    if record is None:
        with performance_span("stage4.overrides.ready_check"):
            ready.require_unchanged(workflow_storage, user)
        return None
    with performance_span("stage4.overrides.manifest_read"):
        manifest = _load_manifest(upload_storage, workflow_storage, user, file_id)
    if manifest is None:
        raise HarmonizationNotReadyError(
            "Harmonization results are incomplete. Return to Stage 3 and run harmonization again."
        )
    require_review_state_matches_manifest(record.value, manifest)
    result = LoadedReviewOverridesResult(
        payload=ReviewOverridesSchema.model_validate(record.value.to_snapshot_payload()),
        version=record.version,
    )
    with performance_span("stage4.overrides.ready_check"):
        ready.require_unchanged(workflow_storage, user)
    return result


def save_review_overrides(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    upload_storage: UploadStorage,
    file_id: DatasetWorkflowId,
    overrides: ReviewOverridePayload,
    review_state: ReviewStateSchema,
    expected_version: VersionToken | None = None,
) -> SaveReviewOverridesResult:
    """Persist one complete active snapshot as append-only review decisions."""
    ready = capture_ready_harmonization(workflow_storage, user, file_id)
    manifest = _load_manifest(upload_storage, workflow_storage, user, file_id)
    if manifest is None:
        raise HarmonizationNotReadyError(
            "Harmonization results are incomplete. Return to Stage 3 and run harmonization again."
        )
    existing_record = load_readable_review_overrides_record(workflow_storage, user, file_id)
    require_review_state_matches_manifest(
        existing_record.value if existing_record is not None else None,
        manifest,
    )
    _validate_review_snapshot(overrides, manifest)
    try:
        saved = save_review_overrides_state(
            workflow_storage,
            user,
            file_id=file_id,
            overrides=_override_payload_to_store(overrides),
            review_state=ReviewProgressState.from_payload(review_state.model_dump(mode="json")),
            expected_version=expected_version,
        )
    except ReviewOverridesStoreConflictError as exc:
        raise ReviewStateConflictError(file_id) from exc
    except ReviewOverridesUnreadableError as exc:
        raise HarmonizationNotReadyError(REVIEW_STATE_RECOVERY_DETAIL) from exc

    result = SaveReviewOverridesResult(
        file_id=file_id,
        updated_at=saved.value.updated_at,
        version=saved.version,
    )
    ready.require_unchanged(workflow_storage, user)
    return result


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
            columns.append(ColumnIdentity(key=row.column_key, header=row.column_name))
    return columns


def _find_unique_non_conformant_values(
    manifest: ManifestSummary,
    column_pv_map: ColumnPvSets,
    review_overrides: ReviewOverrides | None,
) -> list[NonConformantItem]:
    seen: set[tuple[str, str, str]] = set()
    non_conformant: list[NonConformantItem] = []

    for row in manifest.rows:
        pv_set = column_pv_map.get(row.column_key)
        for current_value in _active_values_for_row(row, review_overrides):
            col_key = str(row.column_key)
            # Gate once per unique term/current value pair; repeated source rows
            # should not make reviewers resolve the same problem more than once.
            key = (col_key, row.to_harmonize, current_value)
            if key in seen:
                continue
            seen.add(key)

            if pv_set and current_value and not check_value_conformance(current_value, pv_set):
                non_conformant.append(NonConformantItem(
                    column=row.column_name,
                    value=current_value,
                    original=row.to_harmonize,
                ))

    return non_conformant


def _active_values_for_row(
    row: ManifestRow,
    review_overrides: ReviewOverrides | None,
) -> list[str]:
    return _active_values_for_indices(row, row.row_indices, review_overrides)


def _active_values_for_indices(
    row: ManifestRow,
    row_indices: list[int],
    review_overrides: ReviewOverrides | None,
) -> list[str]:
    baseline_value = _baseline_value_for_row(row)
    if review_overrides is None or not row_indices:
        return [baseline_value]

    values: list[str] = []
    for row_index in row_indices:
        active_value = _active_review_value_for_index(row, row_index, review_overrides)
        values.append(active_value if active_value is not None else baseline_value)
    return values


def _active_review_value_for_index(
    row: ManifestRow,
    row_index: int,
    review_overrides: ReviewOverrides | None,
) -> str | None:
    if review_overrides is None:
        return None
    row_overrides = review_overrides.overrides.get(str(row_index + 1))
    active_override = row_overrides.get(row.column_key) if row_overrides is not None else None
    return active_override.human_value if active_override is not None else None


def _baseline_value_for_row(row: ManifestRow) -> str:
    """Use the source value when the provider made no recommendation."""
    return row.top_harmonization if row.top_harmonization.strip() else row.to_harmonize


def _build_columns_from_manifest(
    manifest: ManifestSummary,
    column_pv_map: ColumnPvSets,
    target_cde_keys: Mapping[ColumnKey, str],
    cde_mappings_by_column: Mapping[ColumnKey, CdeMappingEntry],
    review_overrides: ReviewOverrides | None,
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
        if _is_unchanged_passthrough(mapping_entry, manifest_rows, review_overrides):
            continue
        target_cde_key = target_cde_keys.get(col_key)
        serialized_col_key = str(col_key)
        transformations = [
            transformation
            for row in manifest_rows
            for transformation in _build_transformations(
                row,
                column_pv_map.get(row.column_key),
                review_overrides,
            )
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


def _is_unchanged_passthrough(
    mapping_entry: CdeMappingEntry | None,
    manifest_rows: list[ManifestRow],
    review_overrides: ReviewOverrides | None,
) -> bool:
    return (
        mapping_entry is not None
        and not mapping_entry.maps_values
        and all(
            not is_value_changed(row.to_harmonize, active_value)
            for row in manifest_rows
            for active_value in _active_values_for_row(row, review_overrides)
        )
    )


def _build_transformation(
    row: ManifestRow,
    pv_set: frozenset[str] | None,
    review_overrides: ReviewOverrides | None,
    row_indices: list[int] | None = None,
) -> Transformation:
    original_value = row.to_harmonize or ""
    harmonized_value = row.top_harmonization or None
    is_changed = is_value_changed(original_value, harmonized_value)
    recommendation_type = _compute_recommendation_type(original_value, harmonized_value)

    grouped_row_indices = row.row_indices if row_indices is None else row_indices
    current_values = _active_values_for_indices(row, grouped_row_indices, review_overrides)
    active_review_values = [
        _active_review_value_for_index(row, row_index, review_overrides)
        for row_index in grouped_row_indices
    ]
    distinct_manual_values = {value for value in active_review_values if value is not None}
    manual_override = (
        next(iter(distinct_manual_values))
        if len(distinct_manual_values) == 1 and all(value is not None for value in active_review_values)
        else None
    )
    manifest_indices_full = [idx + 1 for idx in grouped_row_indices]
    return Transformation(
        originalValue=original_value,
        harmonizedValue=harmonized_value,
        matchFidelity=row.match_fidelity,
        isChanged=is_changed,
        recommendationType=recommendation_type.value,
        manualOverride=manual_override,
        isPVConformant=all(check_value_conformance(value, pv_set) for value in current_values),
        pvSetAvailable=pv_set is not None and len(pv_set) > 0,
        topSuggestions=_build_suggestions_with_conformance(row.top_harmonizations, pv_set),
        rowIndices=manifest_indices_full,
    )


def _build_transformations(
    row: ManifestRow,
    pv_set: frozenset[str] | None,
    review_overrides: ReviewOverrides | None,
) -> list[Transformation]:
    """Keep one transformation per active value when review differs by row."""
    if not row.row_indices:
        return [_build_transformation(row, pv_set, review_overrides)]

    row_indices_by_value: dict[str | None, list[int]] = {}
    for row_index in row.row_indices:
        active_value = _active_review_value_for_index(row, row_index, review_overrides)
        row_indices_by_value.setdefault(active_value, []).append(row_index)

    return [
        _build_transformation(row, pv_set, review_overrides, row_indices)
        for row_indices in row_indices_by_value.values()
    ]


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
) -> dict[str, dict[str, dict[str, str]]]:
    return {
        row_key: {
            column_key: {
                "human_value": override.human_value,
                "original_value": override.original_value,
            }
            for column_key, override in columns.items()
        }
        for row_key, columns in overrides.items()
    }


def _validate_review_snapshot(
    overrides: ReviewOverridePayload,
    manifest: ManifestSummary,
) -> None:
    valid_cells = {
        (str(row_index + 1), str(row.column_key)): (
            row.to_harmonize,
            _baseline_value_for_row(row),
        )
        for row in manifest.rows
        for row_index in row.row_indices
    }
    for row_key, columns in overrides.items():
        for column_key, override in columns.items():
            valid_cell = valid_cells.get((row_key, column_key))
            if (
                valid_cell is None
                or override.original_value != valid_cell[0]
                or override.human_value == valid_cell[1]
            ):
                raise InvalidReviewOverrideSelectionError(
                    "A review choice does not match the current harmonization result. Reload Stage 4."
                )


__all__ = [
    "LoadedReviewOverridesResult",
    "InvalidReviewOverrideSelectionError",
    "ReviewStateConflictError",
    "SaveReviewOverridesResult",
    "build_non_conformant_values",
    "build_row_context",
    "build_stage_four_rows",
    "get_review_overrides",
    "save_review_overrides",
]
