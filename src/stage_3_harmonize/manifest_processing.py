"""Persist and normalize a provider manifest before later stages read it."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from src.domain.column_renames import ColumnRenameSet
from src.domain.harmonization import HarmonizationManifestSummary
from src.domain.manifest import ManifestPvAdjustment, ManifestRow, ManifestSummary
from src.domain.pv_validation import compute_pv_adjustment
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_writer import apply_column_renames_batch, apply_pv_adjustments_batch
from src.persistence.pv_manifest_store import ColumnPvSets
from src.stage_3_harmonize.result_summary import build_harmonization_manifest_summary
from src.storage import UploadStorage

# Preserve the established logger name so existing CloudWatch filters keep working.
_logger = logging.getLogger("src.stage_3_harmonize.router")


def _read_manifest_if_exists(manifest_path: Path | None) -> ManifestSummary | None:
    if manifest_path is None or not manifest_path.exists():
        return None
    return read_manifest_parquet(manifest_path)


async def _store_and_adjust_manifest(
    file_id: str,
    manifest_path: Path,
    manifest_data: ManifestSummary,
    storage: UploadStorage,
    column_renames: ColumnRenameSet,
    column_pv_map: ColumnPvSets,
) -> ManifestSummary:
    """Store before adjustment so later stages read the managed, adjusted file."""
    stored_path = storage.save_harmonization_manifest(file_id, manifest_path)
    if stored_path is None:
        _logger.warning("Failed to store manifest", extra={"file_id": file_id})
        return manifest_data

    renamed_count = await _apply_column_renames_to_manifest(stored_path, column_renames)
    if renamed_count > 0:
        _logger.info("Applied column renames", extra={"file_id": file_id, "renamed_count": renamed_count})
        manifest_data = read_manifest_parquet(stored_path) or manifest_data

    adjustment_count = await _apply_pv_adjustments(stored_path, column_pv_map)
    if adjustment_count > 0:
        _logger.info("Applied PV adjustments", extra={"file_id": file_id, "adjustment_count": adjustment_count})
        return read_manifest_parquet(stored_path) or manifest_data

    return manifest_data


async def _apply_column_renames_to_manifest(manifest_path: Path, column_renames: ColumnRenameSet) -> int:
    return await run_in_threadpool(apply_column_renames_batch, manifest_path, column_renames)


def _compute_row_adjustment(row: ManifestRow, pv_set: frozenset[str]) -> ManifestPvAdjustment | None:
    adjusted_value = compute_pv_adjustment(
        original_value=row.to_harmonize,
        top_harmonization=row.top_harmonization,
        top_suggestions=row.top_harmonizations,
        pv_set=pv_set,
    )
    if adjusted_value is None:
        return None
    return ManifestPvAdjustment.from_raw(
        row.column_key,
        row.to_harmonize,
        adjusted_value,
    )


def _process_row_for_adjustment(
    row: ManifestRow,
    column_pv_map: ColumnPvSets,
) -> ManifestPvAdjustment | None:
    """Skip columns without approved values because they need no adjustment."""
    pv_set = column_pv_map.get(row.column_key)
    if not pv_set:
        return None
    return _compute_row_adjustment(row, pv_set)


def _collect_pv_adjustments(
    rows: list[ManifestRow],
    column_pv_map: ColumnPvSets,
) -> list[ManifestPvAdjustment]:
    adjustments = [adjustment for row in rows if (adjustment := _process_row_for_adjustment(row, column_pv_map))]
    _log_non_conformant_samples(rows, column_pv_map)
    return adjustments


def _log_non_conformant_samples(rows: list[ManifestRow], column_pv_map: ColumnPvSets) -> None:
    """Log at most five samples from the first 50 rows."""
    samples = [
        {"column": row.column_name, "value": row.top_harmonization}
        for row in rows[:50]
        if _is_top_harmonization_non_conformant(row, column_pv_map)
    ][:5]
    if samples:
        _logger.warning(
            "Non-conformant values with no PV-compliant alternative",
            extra={"count": len(samples), "samples": samples},
        )


def _is_top_harmonization_non_conformant(row: ManifestRow, column_pv_map: ColumnPvSets) -> bool:
    """Check provider output only for capped diagnostic logging."""
    pv_set = column_pv_map.get(row.column_key)
    return pv_set is not None and row.top_harmonization not in pv_set


async def _apply_pv_adjustments(manifest_path: Path, column_pv_map: ColumnPvSets) -> int:
    """Replace provider values outside the approved value set when possible."""
    if not any(pv_set for pv_set in column_pv_map.values.values()):
        return 0

    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        return 0

    adjustments = _collect_pv_adjustments(summary.rows, column_pv_map)
    if not adjustments:
        return 0

    return await run_in_threadpool(apply_pv_adjustments_batch, manifest_path, adjustments)


async def persist_and_summarize_manifest(
    file_id: str,
    manifest_path: Path | None,
    storage: UploadStorage,
    column_renames: ColumnRenameSet,
    column_pv_map: ColumnPvSets,
    *,
    source_file_name: str,
    reference_model_label: str,
    reference_model_version: str,
) -> HarmonizationManifestSummary | None:
    """Store, normalize, and summarize a provider manifest."""
    manifest_data = _read_manifest_if_exists(manifest_path)
    if manifest_data is None or manifest_path is None:
        return None

    final_data = await _store_and_adjust_manifest(
        file_id,
        manifest_path,
        manifest_data,
        storage,
        column_renames,
        column_pv_map,
    )
    return build_harmonization_manifest_summary(
        final_data,
        column_pv_map,
        source_file_name=source_file_name,
        reference_model_label=reference_model_label,
        reference_model_version=reference_model_version,
    )


__all__ = ["persist_and_summarize_manifest"]
