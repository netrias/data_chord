"""
Read and model harmonization manifest parquet files.

Parse the parquet output from harmonization into typed structures for use
across workflow stages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

ConfidenceBucket = Literal["low", "medium", "high"]

HIGH_CONFIDENCE_THRESHOLD: float = 0.8
MEDIUM_CONFIDENCE_THRESHOLD: float = 0.45


@dataclass(frozen=True)
class ManifestRow:
    """why: represent a single row from the harmonization manifest parquet."""

    job_id: str
    column_id: int
    column_name: str
    to_harmonize: str
    top_harmonization: str
    ontology_id: str | None
    top_harmonizations: list[str]
    confidence_score: float | None
    error: str | None
    row_indices: list[int]


@dataclass(frozen=True)
class ManifestSummary:
    """why: aggregate manifest data for frontend consumption."""

    total_terms: int
    changed_terms: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    rows: list[ManifestRow]


def confidence_bucket(score: float | None) -> ConfidenceBucket:
    """why: classify confidence scores into UI-friendly buckets."""
    if score is None:
        return "low"
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


def read_manifest_parquet(manifest_path: Path) -> ManifestSummary | None:
    """why: load and parse the harmonization manifest into typed structures."""
    if not manifest_path.exists():
        logger.warning("Manifest file not found", extra={"path": str(manifest_path)})
        return None

    try:
        table = pq.read_table(manifest_path)
        rows = _parse_manifest_rows(table)
        return _summarize_manifest(rows)
    except Exception as exc:
        logger.exception("Failed to read manifest parquet", exc_info=exc, extra={"path": str(manifest_path)})
        return None


def _parse_manifest_rows(table: pa.Table) -> list[ManifestRow]:
    """why: convert pyarrow table rows into typed ManifestRow objects."""
    rows: list[ManifestRow] = []
    for batch in table.to_batches():
        for i in range(batch.num_rows):
            row = _extract_row(batch, i)
            rows.append(row)
    return rows


def _extract_row(batch: pa.RecordBatch, index: int) -> ManifestRow:
    """why: extract a single row from a record batch into ManifestRow."""
    return ManifestRow(
        job_id=_get_string(batch, "job_id", index, ""),
        column_id=_get_int(batch, "column_id", index, 0),
        column_name=_get_string(batch, "column_name", index, ""),
        to_harmonize=_get_string(batch, "to_harmonize", index, ""),
        top_harmonization=_get_string(batch, "top_harmonization", index, ""),
        ontology_id=_get_string_nullable(batch, "ontology_id", index),
        top_harmonizations=_get_string_list(batch, "top_harmonizations", index),
        confidence_score=_get_float_nullable(batch, "confidence_score", index),
        error=_get_string_nullable(batch, "error", index),
        row_indices=_get_int_list(batch, "row_indices", index),
    )


def _summarize_manifest(rows: list[ManifestRow]) -> ManifestSummary:
    """why: compute aggregate metrics from manifest rows."""
    changed_count = 0
    high_count = 0
    medium_count = 0
    low_count = 0

    for row in rows:
        original = (row.to_harmonize or "").strip().lower()
        harmonized = (row.top_harmonization or "").strip().lower()
        if original != harmonized:
            changed_count += 1

        bucket = confidence_bucket(row.confidence_score)
        if bucket == "high":
            high_count += 1
        elif bucket == "medium":
            medium_count += 1
        else:
            low_count += 1

    return ManifestSummary(
        total_terms=len(rows),
        changed_terms=changed_count,
        high_confidence_count=high_count,
        medium_confidence_count=medium_count,
        low_confidence_count=low_count,
        rows=rows,
    )


def _get_string(batch: Any, column: str, index: int, default: str) -> str:
    """why: safely extract string value from batch column."""
    if column not in batch.schema.names:
        return default
    value = batch.column(column)[index].as_py()
    return str(value) if value is not None else default


def _get_string_nullable(batch: Any, column: str, index: int) -> str | None:
    """why: safely extract nullable string value from batch column."""
    if column not in batch.schema.names:
        return None
    value = batch.column(column)[index].as_py()
    return str(value) if value is not None else None


def _get_int(batch: Any, column: str, index: int, default: int) -> int:
    """why: safely extract integer value from batch column."""
    if column not in batch.schema.names:
        return default
    value = batch.column(column)[index].as_py()
    return int(value) if value is not None else default


def _get_float_nullable(batch: Any, column: str, index: int) -> float | None:
    """why: safely extract nullable float value from batch column."""
    if column not in batch.schema.names:
        return None
    value = batch.column(column)[index].as_py()
    return float(value) if value is not None else None


def _get_string_list(batch: Any, column: str, index: int) -> list[str]:
    """why: safely extract list of strings from batch column."""
    if column not in batch.schema.names:
        return []
    value = batch.column(column)[index].as_py()
    if value is None:
        return []
    return [str(item) for item in value]


def _get_int_list(batch: Any, column: str, index: int) -> list[int]:
    """why: safely extract list of integers from batch column."""
    if column not in batch.schema.names:
        return []
    value = batch.column(column)[index].as_py()
    if value is None:
        return []
    return [int(item) for item in value]


__all__ = [
    "ConfidenceBucket",
    "ManifestRow",
    "ManifestSummary",
    "confidence_bucket",
    "read_manifest_parquet",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
]
