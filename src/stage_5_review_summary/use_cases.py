"""Use cases for building Stage 5 download packages."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from netrias_client import (
    TabularDataset,
    TabularFormat,
    dataset_from_rows,
    read_tabular,
    write_tabular,
)

from src.app.harmonization_readiness import (
    HarmonizationNotReadyError,
    load_readable_review_overrides_record,
    require_ready_harmonization_workflow,
)
from src.app.session_cache import clear_session_cache
from src.domain.column_outcomes import (
    FinalizedValueOutcome,
    FinalValueReviewStatus,
    FinalValueSource,
    summarize_column_outcomes,
)
from src.domain.manifest import ManifestRow, ManifestSummary
from src.domain.pv_validation import check_value_conformance
from src.domain.review_overrides import CellOverride, ReviewOverrides
from src.persistence.cde_mapping_document_store import load_cde_mapping_json
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets, column_pv_sets
from src.persistence.workflow_artifacts import (
    load_harmonization_manifest_path,
    load_harmonized_output_path,
    load_upload_artifact,
)
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.stage_5_review_summary.schemas import (
    ColumnSummary,
    DatasetSummary,
    StageFiveSummaryResponse,
    TermMapping,
    TransformationStep,
)
from src.storage import UploadedFileMeta, UploadStorage, UserContext, WorkflowStorage


@dataclass(frozen=True)
class DownloadPackage:
    base_name: str
    content: io.BytesIO


def build_summary(
    *,
    file_id: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> StageFiveSummaryResponse:
    loaded_state = require_ready_harmonization_workflow(workflow_storage, user, file_id)

    manifest_path = load_harmonization_manifest_path(upload_storage, workflow_storage, user, file_id)
    if manifest_path is None:
        raise HarmonizationNotReadyError(
            "Harmonization results are incomplete. Return to Stage 3 and run harmonization again."
        )

    manifest_summary = read_manifest_parquet(manifest_path)
    if manifest_summary is None:
        raise HarmonizationNotReadyError(
            "Harmonization results cannot be read. Return to Stage 3 and run harmonization again."
        )

    return _build_summary_from_manifest(
        manifest_summary,
        loaded_state,
        file_id,
        upload_storage,
        workflow_storage,
        user,
    )


def build_download_package(
    *,
    file_id: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> DownloadPackage:
    require_ready_harmonization_workflow(workflow_storage, user, file_id)
    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    if meta is None:
        raise HarmonizationNotReadyError("Upload not found. Return to Stage 1 and upload it again.")

    harmonized_path = _load_harmonized_path(upload_storage, workflow_storage, user, file_id, meta)
    manifest_path = load_harmonization_manifest_path(upload_storage, workflow_storage, user, file_id)
    original_dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)
    harmonized_dataset = read_tabular(harmonized_path, sheet_name=meta.selected_sheet)
    if not original_dataset.columns or not harmonized_dataset.columns:
        raise HarmonizationNotReadyError(
            "The harmonized dataset cannot be read. Return to Stage 3 and run harmonization again."
        )

    review_record = load_readable_review_overrides_record(workflow_storage, user, file_id)
    overrides = review_record.value if review_record is not None else None
    final_dataset = _apply_review_overrides(harmonized_dataset, original_dataset, overrides)
    base_name = _download_base_name(meta, file_id)
    mapping_content = load_cde_mapping_json(file_id, workflow_storage, user)
    zip_buffer = _create_zip_buffer(base_name, final_dataset, manifest_path, meta.saved_path, mapping_content)

    # Session complete: release in-memory cache to prevent unbounded growth.
    clear_session_cache(file_id, owner_user_id=user.user_id)

    return DownloadPackage(base_name=base_name, content=zip_buffer)


def _build_history(
    row: ManifestRow,
    upload_timestamp: datetime | None,
    pv_set: frozenset[str] | None,
) -> list[TransformationStep]:
    """Build chronologically-sorted transformation history.

    top_harmonization already includes any PV adjustments from Stage 3.
    """
    upload_ts_str = upload_timestamp.isoformat() if upload_timestamp else None
    steps: list[TransformationStep] = []

    steps.append(TransformationStep(
        value=row.to_harmonize,
        source="original",
        timestamp=upload_ts_str,
        review_status=_value_review_status(row.to_harmonize, pv_set),
    ))

    effective_ai_value = _effective_ai_value(row)
    if effective_ai_value != row.to_harmonize:
        steps.append(TransformationStep(
            value=effective_ai_value,
            source="ai",
            timestamp=upload_ts_str,
            review_status=_value_review_status(effective_ai_value, pv_set),
        ))

    last_override_value: str | None = None
    for override in row.manual_overrides:
        if override.value == last_override_value:
            continue
        last_override_value = override.value
        steps.append(
            TransformationStep(
                value=override.value,
                source="user",
                timestamp=override.timestamp,
                user_id=override.user_id,
                review_status=_value_review_status(override.value, pv_set),
            )
        )

    return _sort_steps_chronologically(steps)


def _value_review_status(
    value: str,
    pv_set: frozenset[str] | None,
) -> FinalValueReviewStatus:
    if not pv_set:
        return FinalValueReviewStatus.NOT_CHECKED
    if check_value_conformance(value, pv_set):
        return FinalValueReviewStatus.CLEAR
    return FinalValueReviewStatus.NEEDS_ATTENTION


def _sort_steps_chronologically(steps: list[TransformationStep]) -> list[TransformationStep]:
    """Sort steps by timestamp, keeping original first and preserving order for ties."""
    if len(steps) <= 1:
        return steps

    original = steps[0]
    rest = steps[1:]

    def sort_key(step: TransformationStep) -> tuple[int, int]:
        # Original stays first even when upload and AI timestamps tie; the rest
        # should follow the user's edit timeline.
        if step.timestamp is None:
            return (0, 0)
        try:
            dt = datetime.fromisoformat(step.timestamp)
            return (1, int(dt.timestamp() * 1000))
        except (ValueError, TypeError):
            return (0, 0)

    sorted_rest = sorted(rest, key=sort_key)
    return [original, *sorted_rest]


@dataclass
class _MappingInfo:
    """Current mapping facts plus the manifest's independent audit history."""

    history: list[TransformationStep]
    row_count: int


@dataclass(frozen=True, order=True)
class _UniqueTermMapping:
    """Identity for one current column/original/final output mapping."""

    source_column_index: int
    column_key: str
    column_label: str
    original_value: str
    final_value: str
    final_value_source: FinalValueSource
    review_status: FinalValueReviewStatus


def _build_summary_from_manifest(
    summary: ManifestSummary,
    loaded_state: LoadedWorkflowState,
    file_id: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> StageFiveSummaryResponse:
    column_pv_map = column_pv_sets(
        workflow_storage,
        user,
        loaded_state,
        [row.column_key for row in summary.rows],
    )
    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    upload_timestamp = meta.uploaded_at if meta else None
    review_record = load_readable_review_overrides_record(workflow_storage, user, file_id)
    review_overrides = review_record.value if review_record is not None else None

    finalized_outcomes: list[FinalizedValueOutcome] = []
    unique_mappings: dict[_UniqueTermMapping, _MappingInfo] = {}
    for row in summary.rows:
        row_outcomes = _finalized_outcomes_for_manifest_row(
            row,
            column_pv_map,
            review_overrides,
        )
        finalized_outcomes.extend(row_outcomes)
        _track_current_mappings(
            unique_mappings,
            row,
            row_outcomes,
            column_pv_map,
            upload_timestamp,
        )

    column_outcomes = summarize_column_outcomes(finalized_outcomes)
    sorted_mappings = sorted(unique_mappings.items())
    term_mappings = [
        TermMapping(
            column=key.column_label,
            column_key=key.column_key,
            source_column_index=key.source_column_index,
            original_value=key.original_value,
            final_value=key.final_value,
            is_changed=key.original_value != key.final_value,
            final_value_source=key.final_value_source,
            review_status=key.review_status,
            row_count=info.row_count,
            history=info.history,
        )
        for key, info in sorted_mappings
    ]

    return StageFiveSummaryResponse(
        dataset=DatasetSummary(
            filename=meta.original_name if meta else None,
            tabular_format=meta.tabular_format.value if meta else None,
            data_model_key=loaded_state.state.data_model_version.data_model_key,
            external_version_number=(
                loaded_state.state.data_model_version.external_version_number
            ),
        ),
        column_summaries=[
            ColumnSummary(
                column=outcome.column_label,
                column_key=str(outcome.column_key),
                source_column_index=outcome.source_column_index,
                distinct_terms=outcome.total_distinct_values,
                changed_distinct_values=outcome.changed_distinct_values,
                total_rows=outcome.total_rows,
                changed_rows=outcome.changed_rows,
                reviewer_edited_rows=outcome.reviewer_edited_rows,
                non_conformant_values=outcome.non_conformant_distinct_values,
                review_status=outcome.review_status,
                ai_changes=outcome.data_chord_changed_distinct_values,
                manual_changes=outcome.reviewer_changed_distinct_values,
                unchanged=outcome.total_distinct_values - outcome.changed_distinct_values,
            )
            for outcome in column_outcomes
        ],
        term_mappings=term_mappings,
        non_conformant_count=sum(
            outcome.non_conformant_distinct_values for outcome in column_outcomes
        ),
    )


def _finalized_outcomes_for_manifest_row(
    row: ManifestRow,
    column_pv_map: ColumnPvSets,
    review_overrides: ReviewOverrides | None,
) -> list[FinalizedValueOutcome]:
    """Resolve active per-cell overrides into the same output Stage 5 downloads."""
    pv_set = column_pv_map.get(row.column_key)
    effective_ai_value = _effective_ai_value(row)
    row_indices: list[int | None] = list(row.row_indices) if row.row_indices else [None]
    occurrence_counts: dict[tuple[str, FinalValueSource], int] = {}
    for row_index in row_indices:
        current_value = _resolve_current_value(
            row,
            row_index,
            effective_ai_value,
            review_overrides,
        )
        occurrence_counts[current_value] = occurrence_counts.get(current_value, 0) + 1

    return [
        FinalizedValueOutcome(
            column_key=row.column_key,
            source_column_index=row.column_id,
            column_label=row.column_name,
            original_value=row.to_harmonize,
            final_value=final_value,
            final_value_source=final_value_source,
            occurrence_count=occurrence_count,
            pv_set_available=bool(pv_set),
            is_pv_conformant=check_value_conformance(final_value, pv_set),
        )
        for (final_value, final_value_source), occurrence_count in occurrence_counts.items()
    ]


def _resolve_current_value(
    row: ManifestRow,
    row_index: int | None,
    effective_ai_value: str,
    review_overrides: ReviewOverrides | None,
) -> tuple[str, FinalValueSource]:
    active_override = _active_cell_override(review_overrides, row, row_index)
    if active_override is not None:
        return active_override.human_value, FinalValueSource.REVIEWER
    if effective_ai_value != row.to_harmonize:
        return effective_ai_value, FinalValueSource.DATA_CHORD
    return row.to_harmonize, FinalValueSource.SOURCE


def _active_cell_override(
    review_overrides: ReviewOverrides | None,
    row: ManifestRow,
    row_index: int | None,
) -> CellOverride | None:
    if review_overrides is None or row_index is None:
        return None
    row_overrides = review_overrides.overrides.get(str(row_index + 1))
    return row_overrides.get(row.column_key) if row_overrides is not None else None


def _effective_ai_value(row: ManifestRow) -> str:
    """A blank provider recommendation is a source pass-through, as in Stage 3."""
    return row.top_harmonization if row.top_harmonization.strip() else row.to_harmonize


def _track_current_mappings(
    mappings: dict[_UniqueTermMapping, _MappingInfo],
    row: ManifestRow,
    row_outcomes: list[FinalizedValueOutcome],
    column_pv_map: ColumnPvSets,
    upload_timestamp: datetime | None,
) -> None:
    """Group current term mappings while keeping manifest events as audit history."""
    # Empty string means no data; whitespace-only values pass through as semantically significant.
    if not row.to_harmonize:
        return
    pv_set = column_pv_map.get(row.column_key)
    history = _build_history(row, upload_timestamp, pv_set)
    for outcome in row_outcomes:
        key = _UniqueTermMapping(
            source_column_index=outcome.source_column_index,
            column_key=str(outcome.column_key),
            column_label=outcome.column_label,
            original_value=outcome.original_value,
            final_value=outcome.final_value,
            final_value_source=outcome.final_value_source,
            review_status=outcome.review_status,
        )
        existing = mappings.get(key)
        if existing is None:
            mappings[key] = _MappingInfo(
                history=history,
                row_count=outcome.occurrence_count,
            )
        else:
            existing.row_count += outcome.occurrence_count


def _load_harmonized_path(
    storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    meta: UploadedFileMeta,
) -> Path:
    path = load_harmonized_output_path(storage, workflow_storage, user, file_id, meta)
    if path is None:
        raise HarmonizationNotReadyError(
            "Harmonized output is missing. Return to Stage 3 and run harmonization again."
        )
    return path


def _apply_review_overrides(
    harmonized_dataset: TabularDataset,
    original_dataset: TabularDataset,
    overrides: ReviewOverrides | None,
) -> TabularDataset:
    # Stage 4 overrides apply only to export rows. The stored harmonized file
    # remains the AI/PV-adjusted artifact for audit and comparison.
    final_rows = (
        overrides.apply_to_rows(harmonized_dataset.rows, original_dataset)
        if overrides
        else harmonized_dataset.rows
    )
    return dataset_from_rows(
        columns=harmonized_dataset.columns,
        rows=final_rows,
        source_format=harmonized_dataset.source_format,
        sheet_name=harmonized_dataset.sheet_name,
    )


def _download_base_name(meta: UploadedFileMeta, file_id: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    original_stem = Path(meta.original_name).stem
    return f"{original_stem}_{file_id}_{timestamp}"


def _manifest_to_json(manifest_path: Path) -> str | None:
    """JSON enables human inspection of transformation history in the download."""
    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        return None
    return json.dumps([asdict(row) for row in summary.rows], indent=2)


def _create_zip_buffer(
    base_name: str,
    dataset: TabularDataset,
    manifest_path: Path | None,
    template_path: Path | None = None,
    mapping_content: str | None = None,
) -> io.BytesIO:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        temp_path = Path(f"{base_name}{dataset.source_format.suffix}")
        zf.writestr(temp_path.name, _tabular_bytes(dataset, template_path))

        if manifest_path:
            json_content = _manifest_to_json(manifest_path)
            if json_content:
                zf.writestr(f"{base_name}_manifest.json", json_content)
        if mapping_content:
            zf.writestr(f"{base_name}_cde_mapping.json", mapping_content)

    zip_buffer.seek(0)
    return zip_buffer


def _tabular_bytes(dataset: TabularDataset, template_path: Path | None) -> bytes | str:
    if dataset.source_format == TabularFormat.XLSX:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / f"output{dataset.source_format.suffix}"
            write_tabular(output_path, dataset, template_path=template_path)
            return output_path.read_bytes()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=dataset.source_format.delimiter, lineterminator="\n")
    writer.writerow(dataset.headers)
    writer.writerows(dataset.rows)
    return output.getvalue()


__all__ = [
    "DownloadPackage",
    "build_summary",
    "build_download_package",
]
