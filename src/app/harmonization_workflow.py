"""Application workflow for one accepted harmonization run.

This module owns workflow orchestration and artifact publication. It has no
HTTP or API-schema dependencies, so another interface can call the same run.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from netrias_client import read_tabular, write_tabular

from src.app.harmonization_job_state import (
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
from src.domain.reference_data import ReferenceDataRepository
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
from src.persistence.review_override_store import delete_review_overrides_state
from src.persistence.workflow_artifacts import load_upload_artifact, save_harmonized_artifacts
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.storage import UploadStorage, UserContext, VersionToken, WorkflowConflictError, WorkflowFile, WorkflowStorage

logger = logging.getLogger(__name__)


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
        meta = load_upload_artifact(self._upload_storage, self._workflow_storage, user, file_id)
        if meta is None:
            raise FileNotFoundError(file_id)
        artifact_versions = capture_harmonization_artifact_versions(self._workflow_storage, user, file_id)
        workflow_state = loaded_state.state
        manifest = workflow_state.mapping_manifest
        mapping_choices = workflow_state.mapping_choices
        if mapping_choices is None:
            raise ValueError("Workflow mapping choices are incomplete")
        column_overrides = mapping_choices.column_overrides
        column_renames = mapping_choices.column_renames
        data_model_version = workflow_state.data_model_version
        resolved_columns = await _resolved_columns_for_source(
            meta.saved_path,
            column_renames,
            workflow_state.selected_sheet,
        )
        reference_model = await asyncio.to_thread(
            self._reference_data_repository.load_model,
            data_model_version,
        )

        prepared_manifest = manifest.apply_choices(column_overrides, column_renames, reference_model.catalog)
        column_cde_map = prepared_manifest.column_cde_map()
        managed_output_path = self._upload_storage.harmonized_path_for(file_id, meta.saved_path)
        worker_output_path = _worker_scratch_path(managed_output_path, authority.worker_id)
        selected_cde_keys = column_cde_map.cde_keys()
        pv_catalog = CdePvCatalog.from_mapping({
            cde_key: reference_model.pvs.values[cde_key] for cde_key in selected_cde_keys
        })
        column_pv_sets = ColumnPvSets({
            column_key: pv_catalog.get(cde_key) for column_key, cde_key in column_cde_map.mappings.items()
        })
        result = await self._run_harmonizer(
            file_path=meta.saved_path,
            data_model_version=data_model_version,
            prepared_manifest=prepared_manifest,
            column_pv_sets=column_pv_sets,
            output_path=worker_output_path,
            sheet_name=workflow_state.selected_sheet,
            use_cache=use_cache,
        )
        if result.status == HarmonizeStatus.SUCCEEDED:
            authority.require_current()
            authority.require_plan_current()
        harmonized_output_path = result.output_path or worker_output_path
        if result.status == HarmonizeStatus.SUCCEEDED:
            await _apply_column_renames_to_output(harmonized_output_path, column_renames, workflow_state.selected_sheet)
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
        manifest_summary = await _read_and_adjust_manifest(
            result.manifest_path,
            column_renames,
            column_pv_sets,
            source_file_name=meta.original_name,
            reference_model_label=reference_model.label,
            reference_model_version=data_model_version.external_version_number,
        )
        if result.status == HarmonizeStatus.SUCCEEDED:
            if not harmonized_output_path.exists() or result.manifest_path is None or manifest_summary is None:
                raise RuntimeError("Harmonization completed without required output artifacts")
            authority.require_current()
            authority.require_plan_current()
            await asyncio.to_thread(
                save_pv_snapshot_if_unchanged,
                self._workflow_storage, user, loaded_state, pv_catalog,
                expected_version=artifact_versions.pv_manifest,
            )
            authority.require_current()
            authority.require_plan_current()
            await asyncio.to_thread(
                save_cde_mapping_document,
                self._workflow_storage, user, file_id, manifest, column_overrides, column_renames,
                resolved_columns, reference_model.catalog, data_model_version,
                expected_version=artifact_versions.cde_mapping,
            )
            authority.require_current()
            authority.require_plan_current()
            await asyncio.to_thread(
                save_harmonized_artifacts,
                self._workflow_storage, user, file_id, harmonized_output_path, result.manifest_path,
                expected_harmonized_output_version=artifact_versions.harmonized_output,
                expected_manifest_version=artifact_versions.manifest,
            )
            await asyncio.to_thread(
                self._upload_storage.save_harmonization_manifest,
                file_id,
                result.manifest_path,
            )
            await asyncio.to_thread(
                self._upload_storage.restore_harmonized_output,
                file_id,
                meta.saved_path,
                harmonized_output_path,
            )
            authority.require_current()
            authority.require_plan_current()
            await asyncio.to_thread(
                _invalidate_previous_stage_three_review,
                self._workflow_storage, user, file_id, artifact_versions.review_overrides,
            )
        logger.info("Manifest summary result", extra={"file_id": file_id, "has_summary": manifest_summary is not None})
        return HarmonizationWorkflowResult(
            job_id=result.job_id,
            status=result.status,
            detail=result.detail,
            job_id_available=result.job_id_available,
            manifest_summary=manifest_summary,
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
        return await asyncio.to_thread(
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
    source_file_name: str,
    reference_model_label: str,
    reference_model_version: str,
) -> HarmonizationManifestSummary | None:
    if manifest_path is None or not manifest_path.exists():
        return None
    manifest_data = read_manifest_parquet(manifest_path)
    if manifest_data is None:
        return None
    renamed_count = await asyncio.to_thread(
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
        manifest_data, column_pv_map,
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
    dataset = await asyncio.to_thread(read_tabular, output_path, sheet_name)
    renamed = apply_column_renames_to_dataset(dataset, column_renames)
    await asyncio.to_thread(write_tabular, output_path, renamed, output_path)


async def _resolved_columns_for_source(
    source_path: Path,
    column_renames: ColumnRenameSet,
    sheet_name: str | None,
) -> tuple[ResolvedTabularColumn, ...]:
    if not source_path.exists():
        return ()
    dataset = await asyncio.to_thread(read_tabular, source_path, sheet_name)
    return resolve_tabular_columns(dataset, column_renames)


def _worker_scratch_path(managed_path: Path, worker_id: str) -> Path:
    return managed_path.with_stem(f"{managed_path.stem}.{worker_id}")


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
        if (pv_set := column_pv_map.get(row.column_key))
        and (adjustment := _compute_row_adjustment(row, pv_set))
    ]
    _log_non_conformant_samples(rows, column_pv_map)
    return adjustments


def _log_non_conformant_samples(rows: list[ManifestRow], column_pv_map: ColumnPvSets) -> None:
    """Log bounded structural evidence without source headers or data values."""
    samples = [
        row
        for row in rows[:50]
        if (pv_set := column_pv_map.get(row.column_key)) is not None
        and row.top_harmonization not in pv_set
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
    return await asyncio.to_thread(apply_pv_adjustments_batch, manifest_path, adjustments)


def _invalidate_previous_stage_three_review(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    expected_version: VersionToken | None,
) -> None:
    if expected_version is not None:
        try:
            delete_review_overrides_state(workflow_storage, user, file_id, expected_version=expected_version)
        except WorkflowConflictError:
            logger.info("Preserving review overrides changed during harmonization", extra={"file_id": file_id})
            raise StaleHarmonizationWorkerError(file_id) from None
        if workflow_storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES) is not None:
            raise StaleHarmonizationWorkerError(file_id)
    elif workflow_storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES) is not None:
        raise StaleHarmonizationWorkerError(file_id)


__all__ = ["HarmonizationWorkflow"]
