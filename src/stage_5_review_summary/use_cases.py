"""Use cases for building Stage 5 download packages."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from netrias_client import (
    TabularDataset,
    TabularFormat,
    dataset_from_rows,
    read_tabular,
)

from src.app.harmonization_readiness import (
    HarmonizationChangedError,
    HarmonizationNotReadyError,
    ReadyHarmonization,
    capture_ready_harmonization,
    load_readable_review_overrides_record,
    require_review_state_matches_manifest,
)
from src.domain.column_outcomes import (
    FinalizedValueOutcome,
    FinalValueReviewStatus,
    FinalValueSource,
    summarize_column_outcomes,
)
from src.domain.columns import ColumnKey
from src.domain.manifest import ManifestRow, ManifestSummary
from src.domain.pv_validation import check_value_conformance
from src.domain.review_overrides import (
    CellOverride,
    ReviewOverrideAction,
    ReviewOverrideEvent,
    ReviewOverrides,
)
from src.observability.events import performance_span
from src.persistence.cde_mapping_document_store import CdeMappingUnreadableError, load_cde_mapping_json
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets, column_pv_sets
from src.persistence.review_override_store import ReviewOverridesRecord
from src.persistence.tabular_output import write_tabular_changes
from src.persistence.workflow_artifacts import (
    open_generated_artifact,
    open_upload_artifact,
)
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.stage_5_review_summary.schemas import (
    ColumnSummary,
    DatasetSummary,
    NonConformantItem,
    StageFiveSummaryResponse,
    TermMapping,
    TransformationStep,
)
from src.storage import UploadedFileMeta, UploadStorage, UserContext, WorkflowFile, WorkflowStorage


@dataclass(frozen=True)
class DownloadPackage:
    base_name: str
    content: io.BytesIO
    audit_warning: str | None = None


@dataclass(frozen=True)
class ReviewedInputs:
    ready: ReadyHarmonization
    meta: UploadedFileMeta
    original: TabularDataset
    manifest: ManifestSummary | None
    review: ReviewOverridesRecord | None

    @property
    def overrides(self) -> ReviewOverrides | None:
        return self.review.value if self.review else None

    def check_current(self, storage: WorkflowStorage, user: UserContext) -> None:
        with performance_span("stage5.summary.ready_check"):
            self.ready.require_unchanged(storage, user)
            latest = load_readable_review_overrides_record(storage, user, self.meta.file_id)
            if (latest.version if latest else None) != (self.review.version if self.review else None):
                raise HarmonizationChangedError("Review decisions changed. Refresh the summary to try again.")


def _load_inputs(
    file_id: str, uploads: UploadStorage, storage: WorkflowStorage, user: UserContext,
    expected_job_id: str | None = None,
) -> ReviewedInputs:
    with performance_span("stage5.summary.ready_capture"):
        ready = capture_ready_harmonization(storage, user, file_id, expected_job_id)
    with (
        performance_span("stage5.summary.upload_metadata"),
        open_upload_artifact(uploads, storage, user, file_id) as meta,
    ):
        if meta is None:
            raise HarmonizationNotReadyError("Upload not found. Return to Stage 1 and upload it again.")
        with performance_span("stage5.summary.source_row_count"):
            original = read_tabular(meta.saved_path, sheet_name=ready.workflow.state.selected_sheet)
    with performance_span("stage5.summary.manifest_read"):
        with open_generated_artifact(storage, user, file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE) as path:
            manifest = read_manifest_parquet(path) if path else None
    with performance_span("stage5.summary.review_state"):
        review = load_readable_review_overrides_record(storage, user, file_id)
        if review and review.value.run_id and review.value.run_id != ready.job.job.worker_id:
            raise HarmonizationNotReadyError("The saved edits belong to a different harmonization run.")
        if manifest is not None:
            require_review_state_matches_manifest(review.value if review else None, manifest)
        elif review and review.value.run_id is None:
            _check_legacy_review(review.value, original, ready.job.job.started_at)
    return ReviewedInputs(ready, meta, original, manifest, review)


def _check_legacy_review(review: ReviewOverrides, source: TabularDataset, job_started: datetime) -> None:
    """Older reviews have no run ID. Check their creation time and original cells before recovery."""
    if not review.events:
        return
    if review.created_at < job_started:
        raise HarmonizationNotReadyError("The saved edits predate this harmonization run.")
    columns = {column.key: column.index for column in source.columns}
    for event in review.events:
        row = int(event.row_key) - 1
        column = columns.get(event.column_key)
        if column is None or row >= len(source.rows) or source.rows[row][column] != event.original_value:
            raise HarmonizationNotReadyError("The reviewed dataset could not be verified against its saved edits.")


def build_summary(
    *, file_id: str, upload_storage: UploadStorage, workflow_storage: WorkflowStorage, user: UserContext,
) -> StageFiveSummaryResponse:
    inputs = _load_inputs(file_id, upload_storage, workflow_storage, user)
    summary = _summary(inputs, workflow_storage, user, allow_partial=False)
    inputs.check_current(workflow_storage, user)
    return summary


@dataclass(frozen=True)
class PreparedReview:
    job_id: str
    summary: StageFiveSummaryResponse
    download: DownloadPackage


def prepare_review(
    file_id: str, uploads: UploadStorage, storage: WorkflowStorage, user: UserContext,
    expected_job_id: str | None = None,
) -> PreparedReview:
    try:
        return _capture_review(file_id, uploads, storage, user, expected_job_id)
    except HarmonizationChangedError:
        # A concurrent save can finish during export. Retry once before asking for a refresh.
        return _capture_review(file_id, uploads, storage, user, expected_job_id)


def _capture_review(
    file_id: str, uploads: UploadStorage, storage: WorkflowStorage, user: UserContext,
    expected_job_id: str | None = None,
) -> PreparedReview:
    inputs = _load_inputs(file_id, uploads, storage, user, expected_job_id)
    summary = _summary(inputs, storage, user, allow_partial=True)
    package = _package_inputs(inputs, storage, user)
    inputs.check_current(storage, user)
    summary.audit_warning = package.audit_warning
    return PreparedReview(inputs.ready.job.job.polling_job_id, summary, package)


def _summary(inputs: ReviewedInputs, workflow_storage: WorkflowStorage, user: UserContext,
             allow_partial: bool) -> StageFiveSummaryResponse:
    with performance_span("stage5.summary.pv_load"):
        pvs = column_pv_sets(
            workflow_storage, user, inputs.ready.workflow, (col.key for col in inputs.original.columns),
        )
    with performance_span("stage5.summary.summary_build"):
        if inputs.manifest is not None:
            response = _assemble_summary_response(
                inputs.manifest, inputs.ready.workflow, inputs.meta, pvs, inputs.meta.uploaded_at,
                inputs.overrides, len(inputs.original.rows),
            )
        elif allow_partial:
            response = _partial_summary(inputs, pvs, workflow_storage, user)
        else:
            raise HarmonizationNotReadyError(
                "Harmonization results are incomplete. Return to Stage 3 and run harmonization again."
            )
    return response


def _partial_summary(
    inputs: ReviewedInputs, pvs: ColumnPvSets, storage: WorkflowStorage, user: UserContext,
) -> StageFiveSummaryResponse:
    with _open_harmonized_path(storage, user, inputs.meta.file_id) as path:
        harmonized = read_tabular(path, sheet_name=inputs.original.sheet_name)
    final = _apply_review_overrides(harmonized, inputs.original, inputs.overrides)
    warnings: dict[tuple[int, str, str], NonConformantItem] = {}
    for column in final.columns:
        allowed = pvs.get(column.key)
        if not allowed:
            continue
        for row, source in zip(final.rows, inputs.original.rows, strict=True):
            value = row[column.index]
            if not check_value_conformance(value, allowed):
                original = source[column.index]
                warnings[column.index, original, value] = NonConformantItem(
                    column=column.header, source_column_index=column.index, original=original, value=value,
                )
    model = inputs.ready.workflow.state.data_model_version
    return StageFiveSummaryResponse(
        dataset=DatasetSummary(
            filename=inputs.meta.original_name, tabular_format=inputs.meta.tabular_format.value,
            data_model_key=model.data_model_key, external_version_number=model.external_version_number,
        ),
        history_available=False, column_summaries=[], term_mappings=[], non_conformant_items=list(warnings.values()),
    )


def build_download_package(
    *, file_id: str, upload_storage: UploadStorage, workflow_storage: WorkflowStorage, user: UserContext,
) -> DownloadPackage:
    inputs = _load_inputs(file_id, upload_storage, workflow_storage, user)
    package = _package_inputs(inputs, workflow_storage, user)
    inputs.check_current(workflow_storage, user)
    return package


def _package_inputs(
    inputs: ReviewedInputs, storage: WorkflowStorage, user: UserContext,
) -> DownloadPackage:
    file_id = inputs.meta.file_id
    with _open_harmonized_path(storage, user, file_id) as path:
        harmonized = read_tabular(path, sheet_name=inputs.original.sheet_name)
        if not inputs.original.columns or not harmonized.columns:
            raise HarmonizationNotReadyError("The reviewed dataset could not be recovered.")
        final = _apply_review_overrides(harmonized, inputs.original, inputs.overrides)
        try:
            mapping_content = load_cde_mapping_json(file_id, storage, user)
        except CdeMappingUnreadableError:
            mapping_content = None
        return _create_zip_buffer(
            _download_base_name(inputs.meta, file_id), final, inputs.manifest, path, mapping_content, inputs.overrides,
        )


def _build_history(
    row: ManifestRow,
    upload_timestamp: datetime | None,
    pv_set: frozenset[str] | None,
    review_overrides: ReviewOverrides | None = None,
    row_index: int | None = None,
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

    effective_ai_value = row.baseline_value
    if effective_ai_value != row.to_harmonize:
        steps.append(TransformationStep(
            value=effective_ai_value,
            source="ai",
            timestamp=upload_ts_str,
            review_status=_value_review_status(effective_ai_value, pv_set),
        ))

    for event in _events_for_row(review_overrides, row, row_index):
        selected_value = _event_selected_value(event, effective_ai_value)
        steps.append(
            TransformationStep(
                value=selected_value,
                source="user",
                action=event.kind,
                timestamp=_event_timestamp(event),
                review_status=_value_review_status(selected_value, pv_set),
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
    """Current mapping facts plus one row-specific review history."""

    history: list[TransformationStep]
    row_count: int
    row_indices: list[int]


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
    history_signature: tuple[tuple[str, str, str | None, str | None], ...]


def _assemble_summary_response(
    summary: ManifestSummary,
    loaded_state: LoadedWorkflowState,
    meta: UploadedFileMeta | None,
    column_pv_map: ColumnPvSets,
    upload_timestamp: datetime | None,
    review_overrides: ReviewOverrides | None,
    source_row_count: int,
) -> StageFiveSummaryResponse:
    finalized_outcomes: list[FinalizedValueOutcome] = []
    unique_mappings: dict[_UniqueTermMapping, _MappingInfo] = {}
    for row in summary.rows:
        row_outcomes = _finalized_outcomes_for_manifest_row(row, column_pv_map, review_overrides)
        finalized_outcomes.extend(row_outcomes)
        _track_current_mappings(
            unique_mappings,
            row,
            row_outcomes,
            column_pv_map,
            upload_timestamp,
            review_overrides,
        )

    column_outcomes = summarize_column_outcomes(finalized_outcomes)
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
            row_indices=info.row_indices,
            history=info.history,
        )
        for key, info in sorted(unique_mappings.items())
    ]
    non_conformant_items = _non_conformant_items(finalized_outcomes)

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
                total_rows=source_row_count,
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
        non_conformant_items=non_conformant_items,
    )


def _non_conformant_items(
    outcomes: list[FinalizedValueOutcome],
) -> list[NonConformantItem]:
    """Return one warning item per column, source value, and final value."""
    seen: set[tuple[ColumnKey, str, str]] = set()
    items: list[NonConformantItem] = []
    for outcome in outcomes:
        if outcome.review_status is not FinalValueReviewStatus.NEEDS_ATTENTION:
            continue
        identity = (
            outcome.column_key,
            outcome.original_value,
            outcome.final_value,
        )
        if identity in seen:
            continue
        seen.add(identity)
        items.append(
            NonConformantItem(
                column=outcome.column_label,
                source_column_index=outcome.source_column_index,
                value=outcome.final_value,
                original=outcome.original_value,
            )
        )
    return items


def _finalized_outcomes_for_manifest_row(
    row: ManifestRow,
    column_pv_map: ColumnPvSets,
    review_overrides: ReviewOverrides | None,
) -> list[FinalizedValueOutcome]:
    """Resolve active per-cell overrides into the same output Stage 5 downloads."""
    pv_set = column_pv_map.get(row.column_key)
    effective_ai_value = row.baseline_value
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


def _track_current_mappings(
    mappings: dict[_UniqueTermMapping, _MappingInfo],
    row: ManifestRow,
    row_outcomes: list[FinalizedValueOutcome],
    column_pv_map: ColumnPvSets,
    upload_timestamp: datetime | None,
    review_overrides: ReviewOverrides | None = None,
) -> None:
    """Group current mappings without mixing histories from different rows."""
    # Empty string means no data; whitespace-only values pass through as semantically significant.
    if not row.to_harmonize:
        return
    pv_set = column_pv_map.get(row.column_key)
    outcome_by_value = {
        (outcome.final_value, outcome.final_value_source): outcome
        for outcome in row_outcomes
    }
    row_indices = list(row.row_indices) if row.row_indices else [None]
    for row_index in row_indices:
        current_value, current_source = _resolve_current_value(
            row,
            row_index,
            row.baseline_value,
            review_overrides,
        )
        outcome = outcome_by_value[(current_value, current_source)]
        history = _build_history(row, upload_timestamp, pv_set, review_overrides, row_index)
        key = _UniqueTermMapping(
            source_column_index=outcome.source_column_index,
            column_key=str(outcome.column_key),
            column_label=outcome.column_label,
            original_value=outcome.original_value,
            final_value=outcome.final_value,
            final_value_source=outcome.final_value_source,
            review_status=outcome.review_status,
            history_signature=tuple(
                (step.value, step.source, step.action, step.timestamp) for step in history
            ),
        )
        existing = mappings.get(key)
        if existing is None:
            mappings[key] = _MappingInfo(
                history=history,
                row_count=1,
                row_indices=[row_index + 1] if row_index is not None else [],
            )
        else:
            existing.row_count += 1
            if row_index is not None:
                existing.row_indices.append(row_index + 1)


@contextmanager
def _open_harmonized_path(storage: WorkflowStorage, user: UserContext, file_id: str) -> Iterator[Path]:
    with open_generated_artifact(storage, user, file_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        if path is None:
            raise HarmonizationNotReadyError(
                "Harmonized output is missing. Return to Stage 3 and run harmonization again."
            )
        yield path


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
    timestamp = meta.uploaded_at.astimezone(UTC).strftime("%Y%m%d_%H%M%S")
    original_stem = Path(meta.original_name).stem
    return f"{original_stem}_{file_id}_{timestamp}"


def _manifest_to_json(
    summary: ManifestSummary,
    review_overrides: ReviewOverrides | None = None,
) -> str:
    """Project immutable Stage 3 facts and current review decisions as JSON."""
    return json.dumps(
        [_manifest_row_projection(row, review_overrides) for row in summary.rows],
        indent=2,
    )


def _manifest_row_projection(
    row: ManifestRow,
    review_overrides: ReviewOverrides | None,
) -> dict[str, object]:
    """Keep Stage 3 fields immutable and attach row-aware review state."""
    active_values = {
        str(row_index + 1): _resolve_current_value(
            row,
            row_index,
            row.baseline_value,
            review_overrides,
        )[0]
        for row_index in row.row_indices
    }
    return {
        "job_id": row.job_id,
        "column_id": row.column_id,
        "column_name": row.column_name,
        "to_harmonize": row.to_harmonize,
        "top_harmonization": row.top_harmonization,
        "ontology_id": row.ontology_id,
        "top_harmonizations": row.top_harmonizations,
        "match_fidelity": row.match_fidelity.value,
        "error": row.error,
        "row_indices": row.row_indices,
        "active_values": active_values,
        "review_events": [
            _event_to_payload(event)
            for event in _events_for_manifest_row(review_overrides, row)
        ],
    }


def _events_for_manifest_row(
    review_overrides: ReviewOverrides | None,
    row: ManifestRow,
) -> list[ReviewOverrideEvent]:
    if review_overrides is None:
        return []
    row_keys = {str(index + 1) for index in row.row_indices}
    events = review_overrides.term_history.get(row.column_key, {}).get(row.to_harmonize, [])
    return [event for event in events if event.row_key in row_keys]


def _events_for_row(
    review_overrides: ReviewOverrides | None, row: ManifestRow, row_index: int | None,
) -> list[ReviewOverrideEvent]:
    if review_overrides is None or row_index is None:
        return []
    return review_overrides.cell_history.get(row.column_key, {}).get(str(row_index + 1), [])


def _event_selected_value(event: ReviewOverrideEvent, baseline_value: str) -> str:
    if event.kind is ReviewOverrideAction.CLEAR:
        return baseline_value
    assert event.selected_value is not None
    return event.selected_value


def _event_timestamp(event: ReviewOverrideEvent) -> str:
    return event.timestamp.isoformat()


def _event_to_payload(event: ReviewOverrideEvent) -> dict[str, object]:
    return {
        "kind": event.kind.value,
        "row_key": event.row_key,
        "column_key": str(event.column_key),
        "original_value": event.original_value,
        "selected_value": event.selected_value,
        "timestamp": _event_timestamp(event),
    }


def _create_zip_buffer(
    base_name: str,
    dataset: TabularDataset,
    manifest: ManifestSummary | None,
    template_path: Path | None = None,
    mapping_content: str | None = None,
    review_overrides: ReviewOverrides | None = None,
) -> DownloadPackage:
    missing = []
    warning = None
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        temp_path = Path(f"{base_name}{dataset.source_format.suffix}")
        zf.writestr(temp_path.name, _tabular_bytes(dataset, template_path))

        history = _manifest_to_json(manifest, review_overrides) if manifest else None
        for suffix, label, content in [
            ("manifest", "transformation history", history),
            ("cde_mapping", "column mapping", mapping_content),
        ]:
            filename = f"{base_name}_{suffix}.json"
            if content is None:
                missing.append(f"{label} ({filename})")
            else:
                zf.writestr(filename, content)
        if missing:
            warning = (
                "We couldn’t recover the " + " and ".join(missing) + " for this download. "
                "Your reviewed dataset is included. If you need a complete audit, "
                "you may need to run the workflow again."
            )
            zf.writestr("AUDIT-WARNING.txt", warning + "\n")

    zip_buffer.seek(0)
    return DownloadPackage(base_name, zip_buffer, warning)


def _tabular_bytes(dataset: TabularDataset, template_path: Path | None) -> bytes | str:
    if dataset.source_format == TabularFormat.XLSX:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / f"output{dataset.source_format.suffix}"
            assert template_path is not None
            baseline = read_tabular(template_path, sheet_name=dataset.sheet_name)
            write_tabular_changes(output_path, template_path, baseline, dataset)
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
