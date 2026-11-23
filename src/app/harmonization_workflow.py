"""Application workflow for one accepted harmonization run.

This module owns workflow orchestration and artifact publication. It has no
HTTP or API-schema dependencies, so another interface can call the same run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from netrias_client import read_tabular

from src.app.harmonization_job_state import (
    HarmonizationArtifactVersions,
    RunAuthority,
    StaleHarmonizationWorkerError,
    capture_harmonization_artifact_versions,
)
from src.app.harmonization_result_summary import build_harmonization_manifest_summary
from src.app.harmonization_results import HarmonizationWorkflowResult
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_renames import ColumnRenameSet
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import HarmonizationManifestSummary, HarmonizeStatus
from src.domain.manifest import ColumnMappingManifest, ManifestPvAdjustment, ManifestRow
from src.domain.pv_validation import compute_pv_adjustment
from src.domain.reference_data import ReferenceDataRepository, ReferenceModel
from src.domain.review_overrides import ReviewOverrides, ReviewProgressState
from src.domain.tabular_column_renames import (
    ResolvedTabularColumn,
    apply_column_renames_to_dataset,
    resolve_tabular_columns,
)
from src.integrations.harmonize import HarmonizeResult, HarmonizeService
from src.persistence.cde_mapping_document_store import save_cde_mapping_document
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_writer import apply_column_renames_batch, apply_pv_adjustments_batch
from src.persistence.pv_manifest_store import ColumnPvSets, save_pv_snapshot_if_unchanged
from src.persistence.tabular_output import write_tabular_changes
from src.persistence.workflow_artifacts import open_upload_artifact, save_harmonized_artifacts
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.storage import (
    UploadedFileMeta,
    UploadStorage,
    UserContext,
    VersionToken,
    WorkflowConflictError,
    WorkflowFile,
    WorkflowStorage,
)
from src.storage.file_operation import run_file_operation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SourceStructure:
    columns: tuple[ResolvedTabularColumn, ...]
    row_count: int


@dataclass(frozen=True)
class _PreparedRun:
    upload: UploadedFileMeta
    workflow: LoadedWorkflowState
    versions: HarmonizationArtifactVersions
    source: _SourceStructure
    reference: ReferenceModel
    manifest: ColumnMappingManifest
    pv_catalog: CdePvCatalog
    column_pvs: ColumnPvSets


class HarmonizationWorkflow:
    """Run the accepted plan with explicit storage, reference, and provider collaborators."""

    def __init__(
        self,
        *,
        upload_storage: UploadStorage,
        workflow_storage: WorkflowStorage,
        reference_data_repository: ReferenceDataRepository,
        harmonizer: HarmonizeService,
    ) -> None:
        self._upload_storage = upload_storage
        self._workflow_storage = workflow_storage
        self._reference_data_repository = reference_data_repository
        self._harmonizer = harmonizer

    async def run(
        self,
        *,
        file_id: str,
        loaded_state: LoadedWorkflowState,
        authority: RunAuthority,
        user: UserContext,
        use_cache: bool = True,
    ) -> HarmonizationWorkflowResult:
        with (
            open_upload_artifact(self._upload_storage, self._workflow_storage, user, file_id) as meta,
            self._upload_storage.private_workspace(),
        ):
            if meta is None:
                raise FileNotFoundError(file_id)
            prepared = await self._prepare_run(file_id, meta, loaded_state, user)
            worker_path = self._upload_storage.harmonized_path_for(file_id, meta.saved_path)
            result = await self._run_harmonizer(
                file_path=meta.saved_path,
                data_model_version=loaded_state.state.data_model_version,
                prepared_manifest=prepared.manifest,
                column_pv_sets=prepared.column_pvs,
                output_path=worker_path,
                sheet_name=loaded_state.state.selected_sheet,
                use_cache=use_cache,
            )
            return await self._finish_run(file_id, prepared, result, worker_path, authority, user)

    async def _prepare_run(
        self, file_id: str, meta: UploadedFileMeta, loaded: LoadedWorkflowState, user: UserContext,
    ) -> _PreparedRun:
        versions = capture_harmonization_artifact_versions(self._workflow_storage, user, file_id)
        state = loaded.state
        choices = state.mapping_choices
        if choices is None:
            raise ValueError("Workflow mapping choices are incomplete")
        source = await _read_source_structure(meta.saved_path, choices.column_renames, state.selected_sheet)
        reference = await run_file_operation(self._reference_data_repository.load_model, state.data_model_version)
        manifest = state.mapping_manifest.apply_choices(
            choices.column_overrides, choices.column_renames, reference.catalog
        )
        mappings = manifest.column_cde_map()
        pvs = CdePvCatalog.from_mapping({key: reference.pvs.values[key] for key in mappings.cde_keys()})
        column_pvs = ColumnPvSets({column: pvs.get(key) for column, key in mappings.mappings.items()})
        return _PreparedRun(meta, loaded, versions, source, reference, manifest, pvs, column_pvs)

    async def _finish_run(
        self,
        file_id: str,
        prepared: _PreparedRun,
        result: HarmonizeResult,
        worker_path: Path,
        authority: RunAuthority,
        user: UserContext,
    ) -> HarmonizationWorkflowResult:
        state = prepared.workflow.state
        assert state.mapping_choices is not None
        renames = state.mapping_choices.column_renames
        output_path = result.output_path or worker_path
        if result.status == HarmonizeStatus.SUCCEEDED:
            authority.require_publication_authority()
            await _apply_column_renames_to_output(output_path, renames, state.selected_sheet)
        _log_harmonizer_result(file_id, result)
        summary = await _read_and_adjust_manifest(
            result.manifest_path,
            renames,
            prepared.column_pvs,
            source_row_count=prepared.source.row_count,
            source_file_name=prepared.upload.original_name,
            reference_model_label=prepared.reference.label,
            reference_model_version=state.data_model_version.external_version_number,
        )
        if result.status == HarmonizeStatus.SUCCEEDED:
            await self._publish_outputs(file_id, prepared, result, output_path, summary, authority, user)
        logger.info("Manifest summary result", extra={"file_id": file_id, "has_summary": summary is not None})
        return HarmonizationWorkflowResult(
            job_id=result.job_id,
            status=result.status,
            detail=result.detail,
            job_id_available=result.job_id_available,
            manifest_summary=summary,
        )

    async def _publish_outputs(
        self,
        file_id: str,
        prepared: _PreparedRun,
        result: HarmonizeResult,
        output_path: Path,
        summary: HarmonizationManifestSummary | None,
        authority: RunAuthority,
        user: UserContext,
    ) -> None:
        if not output_path.exists() or result.manifest_path is None or summary is None:
            raise RuntimeError("Harmonization completed without required output artifacts")
        state = prepared.workflow.state
        assert state.mapping_choices is not None
        choices = state.mapping_choices
        authority.require_publication_authority()
        await run_file_operation(
            save_pv_snapshot_if_unchanged,
            self._workflow_storage,
            user,
            prepared.workflow,
            prepared.pv_catalog,
            expected_version=prepared.versions.pv_manifest,
        )
        authority.require_publication_authority()
        await run_file_operation(
            save_cde_mapping_document,
            self._workflow_storage,
            user,
            file_id,
            state.mapping_manifest,
            choices.column_overrides,
            choices.column_renames,
            prepared.source.columns,
            prepared.reference.catalog,
            state.data_model_version,
            expected_version=prepared.versions.cde_mapping,
        )
        authority.require_publication_authority()
        await run_file_operation(
            save_harmonized_artifacts,
            self._workflow_storage,
            user,
            file_id,
            output_path,
            result.manifest_path,
            expected_harmonized_output_version=prepared.versions.harmonized_output,
            expected_manifest_version=prepared.versions.manifest,
        )
        authority.require_publication_authority()
        await run_file_operation(
            _start_new_review, self._workflow_storage, user, file_id,
            prepared.versions.review_overrides, authority.worker_id,
        )

    async def _run_harmonizer(
        self,
        *,
        file_path: Path,
        data_model_version: DataModelVersionReference,
        prepared_manifest: ColumnMappingManifest,
        column_pv_sets: ColumnPvSets,
        output_path: Path,
        sheet_name: str | None,
        use_cache: bool,
    ) -> HarmonizeResult:
        """The provider is synchronous; keep its work off the event loop."""
        return await run_file_operation(
            self._harmonizer.run,
            file_path=file_path,
            data_model_version=data_model_version,
            prepared_manifest=prepared_manifest,
            column_pv_sets=column_pv_sets,
            output_path=output_path,
            sheet_name=sheet_name,
            use_cache=use_cache,
        )


async def _read_and_adjust_manifest(
    manifest_path: Path | None,
    column_renames: ColumnRenameSet,
    column_pv_map: ColumnPvSets,
    *,
    source_row_count: int,
    source_file_name: str,
    reference_model_label: str,
    reference_model_version: str,
) -> HarmonizationManifestSummary | None:
    if manifest_path is None or not manifest_path.exists():
        return None
    manifest_data = read_manifest_parquet(manifest_path)
    if manifest_data is None:
        return None
    renamed_count = await run_file_operation(
        apply_column_renames_batch,
        manifest_path,
        column_renames,
    )
    if renamed_count > 0:
        manifest_data = read_manifest_parquet(manifest_path) or manifest_data
    adjustment_count = await _apply_pv_adjustments(manifest_path, column_pv_map)
    if adjustment_count > 0:
        manifest_data = read_manifest_parquet(manifest_path) or manifest_data
    return build_harmonization_manifest_summary(
        manifest_data,
        column_pv_map,
        source_row_count=source_row_count,
        source_file_name=source_file_name,
        reference_model_label=reference_model_label,
        reference_model_version=reference_model_version,
    )


async def _apply_column_renames_to_output(
    output_path: Path,
    column_renames: ColumnRenameSet,
    sheet_name: str | None,
) -> None:
    if not column_renames.renames or not output_path.exists():
        return
    dataset = await run_file_operation(read_tabular, output_path, sheet_name)
    renamed = apply_column_renames_to_dataset(dataset, column_renames)
    await run_file_operation(write_tabular_changes, output_path, output_path, dataset, renamed)


async def _read_source_structure(
    source_path: Path,
    column_renames: ColumnRenameSet,
    sheet_name: str | None,
) -> _SourceStructure:
    if not source_path.exists():
        return _SourceStructure((), 0)
    dataset = await run_file_operation(read_tabular, source_path, sheet_name)
    return _SourceStructure(resolve_tabular_columns(dataset, column_renames), len(dataset.rows))


def _compute_row_adjustment(row: ManifestRow, pv_set: frozenset[str]) -> ManifestPvAdjustment | None:
    adjusted_value = compute_pv_adjustment(
        original_value=row.to_harmonize,
        top_harmonization=row.top_harmonization,
        top_suggestions=row.top_harmonizations,
        pv_set=pv_set,
    )
    if adjusted_value is None:
        return None
    return ManifestPvAdjustment.from_raw(row.column_key, row.to_harmonize, adjusted_value)


def _apply_adjustments(rows: list[ManifestRow], column_pv_map: ColumnPvSets) -> list[ManifestPvAdjustment]:
    adjustments = [
        adjustment
        for row in rows
        if (pv_set := column_pv_map.get(row.column_key)) and (adjustment := _compute_row_adjustment(row, pv_set))
    ]
    _log_non_conformant_samples(rows, column_pv_map)
    return adjustments


def _log_non_conformant_samples(rows: list[ManifestRow], column_pv_map: ColumnPvSets) -> None:
    """Log bounded structural evidence without source headers or data values."""
    samples = [
        row
        for row in rows[:50]
        if (pv_set := column_pv_map.get(row.column_key)) is not None and row.top_harmonization not in pv_set
    ][:5]
    if samples:
        logger.warning(
            "Non-conformant values with no PV-compliant alternative",
            extra={
                "sample_count": len(samples),
                "sample_column_keys": sorted({str(row.column_key) for row in samples}),
                "scanned_row_count": min(len(rows), 50),
            },
        )


async def _apply_pv_adjustments(manifest_path: Path, column_pv_map: ColumnPvSets) -> int:
    if not any(column_pv_map.values.values()):
        return 0
    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        return 0
    adjustments = _apply_adjustments(summary.rows, column_pv_map)
    if not adjustments:
        return 0
    return await run_file_operation(apply_pv_adjustments_batch, manifest_path, adjustments)


def _start_new_review(
    workflow_storage: WorkflowStorage, user: UserContext, file_id: str,
    expected_version: VersionToken | None, run_id: str,
) -> None:
    """Publish a unique empty review so an earlier first-save cannot match this run."""
    now = datetime.now(UTC)
    review = ReviewOverrides(file_id, now, now, (), ReviewProgressState(), run_id)
    try:
        workflow_storage.write_json(
            user, file_id, WorkflowFile.REVIEW_OVERRIDES, review.to_store(), expected_version,
        )
    except WorkflowConflictError as exc:
        raise StaleHarmonizationWorkerError(file_id) from exc


def _log_harmonizer_result(file_id: str, result: HarmonizeResult) -> None:
    logger.info(
        "Harmonization job dispatched",
        extra={
            "file_id": file_id,
            "job_id": result.job_id,
            "status": result.status,
            "manifest_path": str(result.manifest_path),
            "manifest_path_exists": result.manifest_path.exists() if result.manifest_path else False,
        },
    )


__all__ = ["HarmonizationWorkflow"]
