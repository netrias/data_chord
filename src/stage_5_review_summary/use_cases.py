"""Use cases for building Stage 5 download packages."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from netrias_client import (
    TabularDataset,
    TabularFormat,
    dataset_from_rows,
    read_tabular,
    write_tabular,
)

from src.app.session_cache import clear_session_cache
from src.domain import ChangeType
from src.domain.manifest import (
    ManifestRow,
    ManifestSummary,
    get_latest_override_value,
)
from src.domain.pv_validation import check_value_conformance
from src.domain.review_overrides import ReviewOverrides
from src.persistence.cde_mapping_document_store import load_cde_mapping_json
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets, column_pv_sets
from src.persistence.review_override_store import load_review_overrides
from src.persistence.workflow_artifacts import (
    load_harmonization_manifest_path,
    load_harmonized_output_path,
    load_upload_artifact,
)
from src.stage_5_review_summary.schemas import (
    ColumnSummary,
    StageFiveSummaryResponse,
    TermMapping,
    TransformationStep,
)
from src.storage import UploadedFileMeta, UploadStorage, UserContext, WorkflowStorage


class DownloadPackageError(RuntimeError):
    """Base error for Stage 5 download package construction."""

    pass


class UploadNotFoundError(DownloadPackageError):
    pass


class HarmonizedOutputNotFoundError(DownloadPackageError):
    pass


class DownloadDatasetUnreadableError(DownloadPackageError):
    pass


class SummaryManifestNotFoundError(RuntimeError):
    pass


class SummaryManifestUnreadableError(RuntimeError):
    pass


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
    manifest_path = load_harmonization_manifest_path(upload_storage, workflow_storage, user, file_id)
    if manifest_path is None:
        raise SummaryManifestNotFoundError(file_id)

    manifest_summary = read_manifest_parquet(manifest_path)
    if manifest_summary is None:
        raise SummaryManifestUnreadableError(file_id)

    return _build_summary_from_manifest(manifest_summary, file_id, upload_storage, workflow_storage, user)


def build_download_package(
    *,
    file_id: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> DownloadPackage:
    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    if meta is None:
        raise UploadNotFoundError(file_id)

    harmonized_path = _load_harmonized_path(upload_storage, workflow_storage, user, file_id, meta)
    manifest_path = load_harmonization_manifest_path(upload_storage, workflow_storage, user, file_id)
    original_dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)
    harmonized_dataset = read_tabular(harmonized_path, sheet_name=meta.selected_sheet)
    if not original_dataset.columns or not harmonized_dataset.columns:
        raise DownloadDatasetUnreadableError(file_id)

    overrides = load_review_overrides(workflow_storage, user, file_id)
    final_dataset = _apply_review_overrides(harmonized_dataset, original_dataset, overrides)
    base_name = _download_base_name(meta, file_id)
    mapping_content = load_cde_mapping_json(file_id, workflow_storage, user)
    zip_buffer = _create_zip_buffer(base_name, final_dataset, manifest_path, meta.saved_path, mapping_content)

    # Session complete: release in-memory cache to prevent unbounded growth.
    clear_session_cache(file_id)

    return DownloadPackage(base_name=base_name, content=zip_buffer)


def _normalize_for_metrics(value: str | None) -> str:
    """Collapse cosmetic variations for summary counts only.

    Unlike PV conformance checks (which are character-exact per domain rules),
    summary metrics group case/whitespace variants to avoid inflating change
    counts in the UI. The actual exported data preserves exact values.
    """
    if value is None:
        return ""
    return value.strip().lower()


def _classify_change(row: ManifestRow) -> ChangeType:
    original = row.to_harmonize
    ai_value = row.top_harmonization
    latest_override = get_latest_override_value(row.manual_overrides)

    original_norm = _normalize_for_metrics(original)
    ai_norm = _normalize_for_metrics(ai_value)
    override_norm = _normalize_for_metrics(latest_override) if latest_override is not None else None
    final_norm = override_norm if latest_override is not None else ai_norm

    if original_norm == final_norm:
        return ChangeType.UNCHANGED

    if latest_override is not None and override_norm != ai_norm:
        return ChangeType.MANUAL_OVERRIDE

    return ChangeType.AI_HARMONIZED


def _get_final_value(row: ManifestRow) -> str:
    override = get_latest_override_value(row.manual_overrides)
    return override if override is not None else row.top_harmonization


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
        is_pv_conformant=check_value_conformance(row.to_harmonize, pv_set),
    ))

    if row.top_harmonization != row.to_harmonize:
        steps.append(TransformationStep(
            value=row.top_harmonization,
            source="ai",
            timestamp=upload_ts_str,
            is_pv_conformant=check_value_conformance(row.top_harmonization, pv_set),
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
                is_pv_conformant=check_value_conformance(override.value, pv_set),
            )
        )

    return _sort_steps_chronologically(steps)


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


class _MappingInfo(NamedTuple):
    """Immutable container for conformance result and transformation history."""

    is_conformant: bool
    history: list[TransformationStep]


@dataclass(frozen=True, order=True)
class _UniqueTermMapping:
    """Identity for one column/original/final value mapping in the summary."""

    column_key: str
    column_label: str
    original_value: str
    final_value: str


def _process_manifest_row(
    row: ManifestRow,
    ai_counts: dict[int, int],
    manual_counts: dict[int, int],
    unchanged_counts: dict[int, int],
    unique_mappings: dict[_UniqueTermMapping, _MappingInfo],
    column_pv_map: ColumnPvSets,
    upload_timestamp: datetime | None,
) -> None:
    col_id = row.column_id
    change_type = _classify_change(row)

    match change_type:
        case ChangeType.AI_HARMONIZED:
            ai_counts[col_id] += 1
        case ChangeType.MANUAL_OVERRIDE:
            manual_counts[col_id] += 1
        case ChangeType.UNCHANGED:
            unchanged_counts[col_id] += 1

    # Track all rows for conformance checking, not just changed ones.
    _track_mapping(unique_mappings, row, column_pv_map, upload_timestamp)


def _build_summary_from_manifest(
    summary: ManifestSummary,
    file_id: str,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> StageFiveSummaryResponse:
    column_pv_map = column_pv_sets(file_id, [row.column_key for row in summary.rows])
    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    upload_timestamp = meta.uploaded_at if meta else None

    ai_counts: dict[int, int] = defaultdict(int)
    manual_counts: dict[int, int] = defaultdict(int)
    unchanged_counts: dict[int, int] = defaultdict(int)
    distinct_terms: dict[int, int] = defaultdict(int)
    column_names: dict[int, str] = {}
    unique_mappings: dict[_UniqueTermMapping, _MappingInfo] = {}

    for row in summary.rows:
        distinct_terms[row.column_id] += 1
        column_names[row.column_id] = row.column_name
        _process_manifest_row(
            row,
            ai_counts,
            manual_counts,
            unchanged_counts,
            unique_mappings,
            column_pv_map,
            upload_timestamp,
        )

    column_ids = sorted(distinct_terms.keys())
    sorted_mappings = sorted(unique_mappings.items(), key=lambda x: x[0])
    term_mappings = [
        TermMapping(
            column=key.column_label,
            original_value=key.original_value,
            final_value=key.final_value,
            is_pv_conformant=info.is_conformant,
            history=info.history,
        )
        for key, info in sorted_mappings
    ]

    return StageFiveSummaryResponse(
        column_summaries=[
            ColumnSummary(
                column=column_names[col_id],
                distinct_terms=distinct_terms[col_id],
                ai_changes=ai_counts[col_id],
                manual_changes=manual_counts[col_id],
                unchanged=unchanged_counts[col_id],
            )
            for col_id in column_ids
        ],
        term_mappings=term_mappings,
        non_conformant_count=sum(1 for info in unique_mappings.values() if not info.is_conformant),
    )


def _track_mapping(
    mappings: dict[_UniqueTermMapping, _MappingInfo],
    row: ManifestRow,
    column_pv_map: ColumnPvSets,
    upload_timestamp: datetime | None,
) -> None:
    """Deduplicates by (column, original, final) so we check conformance once per unique mapping."""
    # Empty string means no data; whitespace-only values pass through as semantically significant.
    if not row.to_harmonize:
        return
    final = _get_final_value(row)
    key = _UniqueTermMapping(str(row.column_key), row.column_name, row.to_harmonize, final)
    if key in mappings:
        return
    pv_set = column_pv_map.get(row.column_key)
    is_conformant = check_value_conformance(final, pv_set)
    history = _build_history(row, upload_timestamp, pv_set)
    mappings[key] = _MappingInfo(is_conformant, history)


def _load_harmonized_path(
    storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    meta: UploadedFileMeta,
) -> Path:
    path = load_harmonized_output_path(storage, workflow_storage, user, file_id, meta)
    if path is None:
        raise HarmonizedOutputNotFoundError(file_id)
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
    "DownloadDatasetUnreadableError",
    "DownloadPackage",
    "HarmonizedOutputNotFoundError",
    "SummaryManifestNotFoundError",
    "SummaryManifestUnreadableError",
    "UploadNotFoundError",
    "build_summary",
    "build_download_package",
]
