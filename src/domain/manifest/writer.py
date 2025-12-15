"""
Write manual overrides back to harmonization manifest parquet files.

Update existing parquet manifests with user-provided override values while
preserving all original harmonization data.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.domain.manifest.models import ManifestRow, ManualOverride
from src.domain.manifest.reader import read_manifest_parquet

logger = logging.getLogger(__name__)


def add_manual_override(
    manifest_path: Path,
    column_name: str,
    to_harmonize: str,
    override_value: str,
    user_id: str | None = None,
) -> bool:
    """why: append a manual override to a manifest entry and persist to parquet."""
    summary = read_manifest_parquet(manifest_path)
    if summary is None:
        logger.warning("Cannot add override: manifest not found", extra={"path": str(manifest_path)})
        return False

    updated_rows = _apply_override(
        rows=summary.rows,
        column_name=column_name,
        to_harmonize=to_harmonize,
        override_value=override_value,
        user_id=user_id,
    )

    return _write_manifest_parquet(manifest_path, updated_rows)


def _apply_override(
    rows: list[ManifestRow],
    column_name: str,
    to_harmonize: str,
    override_value: str,
    user_id: str | None,
) -> list[ManifestRow]:
    """why: find matching row and append override to its manual_overrides list."""
    timestamp = datetime.now(UTC).isoformat()
    new_override = ManualOverride(user_id=user_id, timestamp=timestamp, value=override_value)

    updated: list[ManifestRow] = []
    for row in rows:
        if row.column_name == column_name and row.to_harmonize == to_harmonize:
            updated_overrides = [*row.manual_overrides, new_override]
            updated_row = ManifestRow(
                job_id=row.job_id,
                column_id=row.column_id,
                column_name=row.column_name,
                to_harmonize=row.to_harmonize,
                top_harmonization=row.top_harmonization,
                ontology_id=row.ontology_id,
                top_harmonizations=row.top_harmonizations,
                confidence_score=row.confidence_score,
                error=row.error,
                row_indices=row.row_indices,
                manual_overrides=updated_overrides,
            )
            updated.append(updated_row)
        else:
            updated.append(row)
    return updated


def _write_manifest_parquet(manifest_path: Path, rows: list[ManifestRow]) -> bool:
    """why: serialize ManifestRow list to parquet with manual_overrides schema."""
    try:
        table = _rows_to_table(rows)
        pq.write_table(table, manifest_path)
        logger.info("Wrote manifest with overrides", extra={"path": str(manifest_path), "row_count": len(rows)})
        return True
    except Exception as exc:
        logger.exception("Failed to write manifest parquet", exc_info=exc, extra={"path": str(manifest_path)})
        return False


def _rows_to_table(rows: list[ManifestRow]) -> pa.Table:
    """why: convert ManifestRow list to pyarrow Table with proper schema."""
    data: dict[str, list[Any]] = {
        "job_id": [],
        "column_id": [],
        "column_name": [],
        "to_harmonize": [],
        "top_harmonization": [],
        "ontology_id": [],
        "top_harmonizations": [],
        "confidence_score": [],
        "error": [],
        "row_indices": [],
        "manual_overrides": [],
    }

    for row in rows:
        data["job_id"].append(row.job_id)
        data["column_id"].append(row.column_id)
        data["column_name"].append(row.column_name)
        data["to_harmonize"].append(row.to_harmonize)
        data["top_harmonization"].append(row.top_harmonization)
        data["ontology_id"].append(row.ontology_id)
        data["top_harmonizations"].append(row.top_harmonizations)
        data["confidence_score"].append(row.confidence_score)
        data["error"].append(row.error)
        data["row_indices"].append(row.row_indices)
        data["manual_overrides"].append([asdict(o) for o in row.manual_overrides])

    override_struct = pa.struct([
        ("user_id", pa.string()),
        ("timestamp", pa.string()),
        ("value", pa.string()),
    ])

    schema = pa.schema([
        ("job_id", pa.string()),
        ("column_id", pa.int64()),
        ("column_name", pa.string()),
        ("to_harmonize", pa.string()),
        ("top_harmonization", pa.string()),
        ("ontology_id", pa.string()),
        ("top_harmonizations", pa.list_(pa.string())),
        ("confidence_score", pa.float64()),
        ("error", pa.string()),
        ("row_indices", pa.list_(pa.int64())),
        ("manual_overrides", pa.list_(override_struct)),
    ])

    return pa.Table.from_pydict(data, schema=schema)


__all__ = [
    "add_manual_override",
]
