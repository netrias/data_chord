"""
Analyze CSV structure and infer column types for upload preview.

Re-exports storage classes from domain for backward compatibility.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from src.domain.manifest import completeness_bucket
from src.domain.storage import (
    UnsupportedUploadError,
    UploadConstraints,
    UploadedFileMeta,
    UploadError,
    UploadStorage,
    UploadTooLargeError,
    describe_constraints,
)

from .schemas import ColumnPreview

logger = logging.getLogger(__name__)
DEFAULT_CDE_SAMPLE_LIMIT = 50
CSVRow = dict[str, str | None]

__all__ = [
    "UploadConstraints",
    "UploadedFileMeta",
    "UploadError",
    "UploadStorage",
    "UploadTooLargeError",
    "UnsupportedUploadError",
    "describe_constraints",
    "analyze_columns",
    "build_cde_payload",
]


def analyze_columns(csv_path: Path, max_preview_rows: int = 5) -> tuple[int, list[ColumnPreview]]:
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    total_rows, headers, sample_rows = _read_csv_sample(csv_path, max_preview_rows)
    columns = [_analyze_single_column(header, sample_rows) for header in headers]
    return total_rows, columns


def _read_csv_sample(csv_path: Path, max_rows: int) -> tuple[int, list[str], list[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        total_rows = 0
        sample_rows: list[dict[str, str]] = []
        for row in reader:
            total_rows += 1
            if len(sample_rows) < max_rows:
                sample_rows.append(row)
    return total_rows, headers, sample_rows


def _analyze_single_column(header: str, sample_rows: list[dict[str, str]]) -> ColumnPreview:
    samples = [_normalize_sample(row.get(header, "")) for row in sample_rows]
    non_empty_values = [value for value in samples if value]
    non_empty_count = len(non_empty_values)
    sample_size = max(len(samples), 1)

    return ColumnPreview(
        column_name=header,
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


def build_cde_payload(
    csv_path: Path,
    headers: list[str],
    limit: int = DEFAULT_CDE_SAMPLE_LIMIT,
) -> dict[str, list[str]]:
    if not headers:
        return {}

    payload: dict[str, list[str]] = {header: [] for header in headers}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        total = 0
        for row_raw in reader:
            row: CSVRow = row_raw
            for header in headers:
                payload[header].append(_normalize_sample(row.get(header, "")))
            total += 1
            if total >= limit:
                break

    max_length = max((len(values) for values in payload.values()), default=0)
    for values in payload.values():
        if len(values) < max_length:
            values.extend([""] * (max_length - len(values)))

    return payload
