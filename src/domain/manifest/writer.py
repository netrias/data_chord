"""
Apply manual overrides and PV adjustments to harmonization manifests.

Handles two write paths: manual overrides (with audit trail) and PV
adjustments (transparent replacement of top_harmonization).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import pyarrow as pa
import pyarrow.parquet as pq

from src.domain.manifest.models import ManifestRow, ManualOverride, get_manifest_schema
from src.domain.manifest.reader import read_manifest_parquet

logger = logging.getLogger(__name__)


class AdjustmentResult(NamedTuple):
    rows: list[ManifestRow]
    adjustment_count: int


def add_manual_overrides_batch(
    manifest_path: Path,
    overrides: list[tuple[str, str, str]],
    user_id: str | None = None,
) -> bool:
    """Single read/write avoids N parquet rewrites when applying N overrides."""
    if not overrides:
        return True

    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        logger.warning("Cannot add overrides: manifest not found", extra={"path": str(manifest_path)})
        return False

    timestamp = datetime.now(UTC).isoformat()
    updated_rows = summary.rows

    for column_name, to_harmonize, override_value in overrides:
        new_override = ManualOverride(user_id=user_id, timestamp=timestamp, value=override_value)
        updated_rows = _apply_single_override(updated_rows, column_name, to_harmonize, new_override)

    return _write_manifest_parquet(manifest_path, updated_rows)


def _apply_single_override(
    rows: list[ManifestRow],
    column_name: str,
    to_harmonize: str,
    new_override: ManualOverride,
) -> list[ManifestRow]:
    updated: list[ManifestRow] = []
    for row in rows:
        if row.column_name == column_name and row.to_harmonize == to_harmonize:
            updated_overrides = [*row.manual_overrides, new_override]
            updated.append(replace(row, manual_overrides=updated_overrides))
        else:
            updated.append(row)
    return updated


def _build_adjustment_map(
    adjustments: list[tuple[str, str, str, str]],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Dict lookup avoids O(n²) scan when matching rows to adjustments."""
    return {(col, to_harm): (adjusted, source) for col, to_harm, adjusted, source in adjustments}


def _apply_adjustments_to_rows(
    rows: list[ManifestRow],
    adjustment_map: dict[tuple[str, str], tuple[str, str]],
) -> AdjustmentResult:
    updated: list[ManifestRow] = []
    adjusted_count = 0
    for row in rows:
        key = (row.column_name, row.to_harmonize)
        if key in adjustment_map:
            adjusted_value, _source = adjustment_map[key]
            updated.append(replace(row, top_harmonization=adjusted_value))
            adjusted_count += 1
        else:
            updated.append(row)
    return AdjustmentResult(updated, adjusted_count)


def apply_pv_adjustments_batch(
    manifest_path: Path,
    adjustments: list[tuple[str, str, str, str]],
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


def _write_manifest_parquet(manifest_path: Path, rows: list[ManifestRow]) -> bool:
    try:
        table = _rows_to_table(rows)
        pq.write_table(table, manifest_path)
        logger.info("Wrote manifest with overrides", extra={"path": str(manifest_path), "row_count": len(rows)})
        return True
    except Exception as exc:
        logger.exception("Failed to write manifest parquet", exc_info=exc, extra={"path": str(manifest_path)})
        return False


_MANIFEST_FIELDS = (
    "job_id",
    "column_id",
    "column_name",
    "to_harmonize",
    "top_harmonization",
    "ontology_id",
    "top_harmonizations",
    "confidence_score",
    "error",
    "row_indices",
)


def _rows_to_table(rows: list[ManifestRow]) -> pa.Table:
    data: dict[str, list[Any]] = {field: [] for field in _MANIFEST_FIELDS}
    data["manual_overrides"] = []

    for row in rows:
        for field in _MANIFEST_FIELDS:
            data[field].append(getattr(row, field))
        data["manual_overrides"].append([asdict(o) for o in row.manual_overrides])

    return pa.Table.from_pydict(data, schema=get_manifest_schema())


__all__ = [
    "add_manual_overrides_batch",
    "apply_pv_adjustments_batch",
]
