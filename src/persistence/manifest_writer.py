"""Apply PV adjustments and column renames to harmonization manifests."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

import pyarrow as pa
import pyarrow.parquet as pq

from src.domain.column_renames import ColumnRenameSet
from src.domain.manifest.adjustments import ManifestPvAdjustment, ManifestTermKey
from src.domain.manifest.models import ManifestRow
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_schema import MANUAL_OVERRIDES_FIELD, get_manifest_schema

logger = logging.getLogger(__name__)


class AdjustmentResult(NamedTuple):
    rows: list[ManifestRow]
    adjustment_count: int


def _build_adjustment_map(
    adjustments: list[ManifestPvAdjustment],
) -> dict[ManifestTermKey, ManifestPvAdjustment]:
    """Dict lookup avoids O(n²) scan when matching rows to adjustments."""
    return {adjustment.term_key: adjustment for adjustment in adjustments}


def _apply_adjustments_to_rows(
    rows: list[ManifestRow],
    adjustment_map: dict[ManifestTermKey, ManifestPvAdjustment],
) -> AdjustmentResult:
    updated: list[ManifestRow] = []
    adjusted_count = 0
    for row in rows:
        adjustment = adjustment_map.get(ManifestTermKey.from_row(row))
        if adjustment is not None:
            updated.append(replace(row, top_harmonization=adjustment.adjusted_value))
            adjusted_count += 1
        else:
            updated.append(row)
    return AdjustmentResult(updated, adjusted_count)


def apply_pv_adjustments_batch(
    manifest_path: Path,
    adjustments: list[ManifestPvAdjustment],
) -> int:
    if not adjustments:
        return 0

    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        logger.warning("Cannot apply PV adjustments: manifest not found", extra={"path": str(manifest_path)})
        return 0

    adjustment_map = _build_adjustment_map(adjustments)
    result = _apply_adjustments_to_rows(summary.rows, adjustment_map)

    if result.adjustment_count > 0 and not _write_manifest_parquet(manifest_path, result.rows):
        return 0

    logger.info(
        "Applied PV adjustments to manifest",
        extra={"path": str(manifest_path), "adjustment_count": result.adjustment_count},
    )
    return result.adjustment_count


def apply_column_renames_batch(manifest_path: Path, renames: ColumnRenameSet) -> int:
    """Apply Stage 2 output names to manifest display metadata by column key."""
    if not renames.renames:
        return 0

    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        logger.warning("Cannot apply column renames: manifest not found", extra={"path": str(manifest_path)})
        return 0

    updated: list[ManifestRow] = []
    renamed_count = 0
    for row in summary.rows:
        output_name = renames.renames.get(row.column_key)
        if output_name is None or output_name == row.column_name:
            updated.append(row)
            continue
        updated.append(replace(row, column_name=output_name))
        renamed_count += 1

    if renamed_count > 0 and not _write_manifest_parquet(manifest_path, updated):
        return 0

    logger.info(
        "Applied column renames to manifest",
        extra={"path": str(manifest_path), "renamed_count": renamed_count},
    )
    return renamed_count


def _write_manifest_parquet(manifest_path: Path, rows: list[ManifestRow]) -> bool:
    try:
        table = _rows_to_table(rows)
        pq.write_table(table, manifest_path)
        logger.info("Wrote manifest", extra={"path": str(manifest_path), "row_count": len(rows)})
        return True
    except Exception as exc:
        logger.exception("Failed to write manifest parquet", exc_info=exc, extra={"path": str(manifest_path)})
        return False


def write_manifest_parquet(manifest_path: Path, rows: list[ManifestRow]) -> bool:
    """Write the canonical manifest shape at a provider boundary."""
    return _write_manifest_parquet(manifest_path, rows)


_MANIFEST_FIELDS = tuple(
    field_name
    for field_name in get_manifest_schema().names
    if field_name != MANUAL_OVERRIDES_FIELD
)


def _rows_to_table(rows: list[ManifestRow]) -> pa.Table:
    data: dict[str, list[Any]] = {field: [] for field in _MANIFEST_FIELDS}
    data[MANUAL_OVERRIDES_FIELD] = []

    for row in rows:
        for field in _MANIFEST_FIELDS:
            data[field].append(getattr(row, field))
        # The provider still requires this legacy field, but review state is
        # persisted in the review override store instead of the manifest.
        data[MANUAL_OVERRIDES_FIELD].append([])

    return pa.Table.from_pydict(data, schema=get_manifest_schema())


__all__ = [
    "apply_column_renames_batch",
    "apply_pv_adjustments_batch",
    "write_manifest_parquet",
]
