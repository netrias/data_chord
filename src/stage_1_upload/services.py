"""Analyze tabular structure and infer column types for upload preview."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from netrias_client import TabularColumn, read_tabular

from src.domain.manifest import completeness_bucket

from .schemas import ColumnPreview


def analyze_columns(csv_path: Path, max_preview_rows: int = 5) -> tuple[int, list[ColumnPreview]]:
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    dataset = read_tabular(csv_path)
    sample_rows = dataset.rows[:max_preview_rows]
    columns = [_analyze_single_column(column, sample_rows) for column in dataset.columns]
    total_rows = len(dataset.rows)
    return total_rows, columns


def _analyze_single_column(column: TabularColumn, sample_rows: list[list[str]]) -> ColumnPreview:
    samples = [_normalize_sample(row[column.index] if column.index < len(row) else "") for row in sample_rows]
    non_empty_values = [value for value in samples if value]
    non_empty_count = len(non_empty_values)
    sample_size = max(len(samples), 1)

    return ColumnPreview(
        column_name=column.header,
        column_key=column.key,
        source_index=column.index,
        header=column.header,
        inferred_type=_infer_type(non_empty_values),
        sample_values=samples,
        confidence_bucket=completeness_bucket(non_empty_count, sample_size),
        confidence_score=round(non_empty_count / sample_size, 2),
    )


def _normalize_sample(value: str | None) -> str:
    """Truncate to 80 chars to prevent oversized UI tooltips."""
    if value is None:
        return ""
    sanitized = value.strip()
    return sanitized[:80]


def _infer_type(values: Iterable[str]) -> str:
    cleaned = [value.replace(",", "") for value in values if value]
    if cleaned and _looks_numeric(cleaned):
        return "numeric"
    if cleaned and _looks_date(cleaned):
        return "date"
    if cleaned:
        return "text"
    return "unknown"


def _looks_numeric(values: list[str]) -> bool:
    try:
        for value in values:
            float(value)
        return True
    except ValueError:
        return False


def _looks_date(values: list[str]) -> bool:
    if not values:
        return False
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"]
    return all(_matches_any_date_format(candidate, formats) for candidate in values)


def _matches_any_date_format(value: str, formats: list[str]) -> bool:
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False
