"""
Parse harmonization manifests into typed structures for use across stages.

Encapsulates parquet deserialization and row extraction logic.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.domain.harmonization import MatchFidelity
from src.domain.manifest.models import ManifestRow, ManifestSummary, is_value_changed
from src.persistence.manifest_schema import MANUAL_OVERRIDES_FIELD, get_manifest_schema

logger = logging.getLogger(__name__)


def read_manifest_parquet(manifest_path: Path) -> ManifestSummary | None:
    if not manifest_path.exists():
        logger.warning("Manifest file not found", extra={"path": str(manifest_path)})
        return None

    try:
        table = pq.read_table(manifest_path)
        _validate_manifest_schema(table.schema)
        rows = _parse_manifest_rows(table)
        return _summarize_manifest(rows)
    except Exception as exc:
        logger.exception("Failed to read manifest parquet", exc_info=exc, extra={"path": str(manifest_path)})
        return None


def _validate_manifest_schema(actual: pa.Schema) -> None:
    """Require provider fields while allowing compatible numeric widths."""
    required_fields = set(get_manifest_schema().names) - {MANUAL_OVERRIDES_FIELD, "match_fidelity"}
    missing_fields = required_fields - set(actual.names)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"Manifest is missing required fields: {missing}")


def _parse_manifest_rows(table: pa.Table) -> list[ManifestRow]:
    rows: list[ManifestRow] = []
    for batch in table.to_batches():
        for i in range(batch.num_rows):
            row = _extract_row(batch, i)
            rows.append(row)
    return rows


def _extract_row(batch: pa.RecordBatch, index: int) -> ManifestRow:
    """Strip AI output fields to remove artifact whitespace from harmonization service."""
    raw_harmonization = _get_string(batch, "top_harmonization", index, "")
    raw_suggestions = _get_string_list(batch, "top_harmonizations", index)
    return ManifestRow(
        job_id=_get_string(batch, "job_id", index, ""),
        column_id=_get_int(batch, "column_id", index, 0),
        column_name=_get_string(batch, "column_name", index, ""),
        to_harmonize=_get_string(batch, "to_harmonize", index, ""),
        top_harmonization=raw_harmonization.strip(),
        ontology_id=_get_string_nullable(batch, "ontology_id", index),
        top_harmonizations=[s.strip() for s in raw_suggestions],
        match_fidelity=_get_match_fidelity(batch, index),
        error=_get_string_nullable(batch, "error", index),
        row_indices=_get_int_list(batch, "row_indices", index),
    )


def _summarize_manifest(rows: list[ManifestRow]) -> ManifestSummary:
    changed_count = 0
    for row in rows:
        if is_value_changed(row.to_harmonize, row.top_harmonization):
            changed_count += 1

    return ManifestSummary(
        total_terms=len(rows),
        changed_terms=changed_count,
        rows=rows,
    )


def _get_string(batch: pa.RecordBatch, column: str, index: int, default: str) -> str:
    if column not in batch.schema.names:
        return default
    value = batch.column(column)[index].as_py()
    return str(value) if value is not None else default


def _get_string_nullable(batch: pa.RecordBatch, column: str, index: int) -> str | None:
    if column not in batch.schema.names:
        return None
    value = batch.column(column)[index].as_py()
    return str(value) if value is not None else None


def _get_int(batch: pa.RecordBatch, column: str, index: int, default: int) -> int:
    if column not in batch.schema.names:
        return default
    value = batch.column(column)[index].as_py()
    return int(value) if value is not None else default


def _get_match_fidelity(batch: pa.RecordBatch, index: int) -> MatchFidelity:
    value = _get_string_nullable(batch, "match_fidelity", index)
    if value is None:
        return MatchFidelity.NONE
    try:
        return MatchFidelity(value)
    except ValueError as exc:
        raise ValueError(f"Unknown match fidelity: {value}") from exc


def _get_string_list(batch: pa.RecordBatch, column: str, index: int) -> list[str]:
    if column not in batch.schema.names:
        return []
    value = batch.column(column)[index].as_py()
    if value is None:
        return []
    return [str(item) for item in value]


def _get_int_list(batch: pa.RecordBatch, column: str, index: int) -> list[int]:
    if column not in batch.schema.names:
        return []
    value = batch.column(column)[index].as_py()
    if value is None:
        return []
    return [int(item) for item in value]


__all__ = [
    "read_manifest_parquet",
]
