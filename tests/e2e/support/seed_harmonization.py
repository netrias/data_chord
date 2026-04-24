"""Seed harmonized CSV and manifest parquet for Playwright E2E tests."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# noqa: E402 - ensure repo root is on sys.path before importing application modules.
from src.domain.manifest import get_manifest_schema  # noqa: E402


def _resolve_upload_base_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    from src.domain.dependencies import UPLOAD_BASE_DIR

    return UPLOAD_BASE_DIR


def _find_original_path(file_id: str, upload_base_dir: Path) -> Path:
    files_dir = upload_base_dir / "files"
    matches = sorted(files_dir.glob(f"{file_id}.*"))
    if not matches:
        raise FileNotFoundError(f"No uploaded file found for {file_id}")
    return matches[0]


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        headers = next(reader, [])
        rows = list(reader)
    return headers, rows


def _write_harmonized(path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    harmonized_path = path.with_name(f"{path.stem}.harmonized.csv")
    with harmonized_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return harmonized_path


def _cell(row: list[str], column_id: int) -> str:
    if 0 <= column_id < len(row):
        return row[column_id]
    return ""


def _build_manifest_rows(
    headers: list[str],
    original_rows: list[list[str]],
    harmonized_rows: list[list[str]],
    changes: dict[int, dict[str, str]],
    file_id: str,
) -> list[dict[str, Any]]:
    columns_with_changes: set[int] = set()
    for row in changes.values():
        for col in row:
            column_id = _resolve_column_id(headers, col)
            if column_id is not None:
                columns_with_changes.add(column_id)
    if not columns_with_changes:
        columns_with_changes = set(range(min(len(headers), 2))) if len(headers) >= 2 else set(range(len(headers)))

    manifest_rows: list[dict[str, Any]] = []
    for column_id in sorted(columns_with_changes):
        col_name = headers[column_id] if 0 <= column_id < len(headers) else ""
        grouped: dict[str, dict[str, Any]] = {}
        for row_idx, original_row in enumerate(original_rows):
            original_value = _cell(original_row, column_id)
            harmonized_value = _cell(harmonized_rows[row_idx], column_id) or original_value
            if original_value not in grouped:
                grouped[original_value] = {
                    "job_id": f"e2e-job-{file_id}",
                    "column_id": column_id,
                    "column_name": col_name,
                    "to_harmonize": original_value,
                    "top_harmonization": harmonized_value,
                    "ontology_id": None,
                    "top_harmonizations": [harmonized_value] if harmonized_value else [],
                    "confidence_score": 0.95 if original_value != harmonized_value else 0.99,
                    "error": None,
                    "row_indices": [row_idx],
                    "manual_overrides": [],
                }
            else:
                grouped[original_value]["row_indices"].append(row_idx)
        manifest_rows.extend(grouped.values())
    return manifest_rows


def _write_manifest(file_id: str, manifest_rows: list[dict[str, Any]], upload_base_dir: Path) -> Path:
    manifest_dir = upload_base_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{file_id}_harmonization.parquet"
    schema = get_manifest_schema()
    table = pa.table({
        "job_id": [row.get("job_id", "e2e-job") for row in manifest_rows],
        "column_id": [row.get("column_id", 0) for row in manifest_rows],
        "column_name": [row.get("column_name", "") for row in manifest_rows],
        "to_harmonize": [row.get("to_harmonize", "") for row in manifest_rows],
        "top_harmonization": [row.get("top_harmonization", "") for row in manifest_rows],
        "ontology_id": [row.get("ontology_id") for row in manifest_rows],
        "top_harmonizations": [row.get("top_harmonizations", []) for row in manifest_rows],
        "confidence_score": [row.get("confidence_score") for row in manifest_rows],
        "error": [row.get("error") for row in manifest_rows],
        "row_indices": [row.get("row_indices", []) for row in manifest_rows],
        "manual_overrides": [row.get("manual_overrides", []) for row in manifest_rows],
    }, schema=schema)
    pq.write_table(table, manifest_path)
    return manifest_path


def _parse_changes(raw: str | None) -> dict[int, dict[str, str]]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    return {int(key): value for key, value in parsed.items()}


def _resolve_column_id(headers: list[str], column_key: str) -> int | None:
    if column_key.isdigit():
        column_id = int(column_key)
        return column_id if 0 <= column_id < len(headers) else None
    if column_key in headers:
        return headers.index(column_key)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--changes", default=None)
    parser.add_argument("--no-manifest", action="store_true")
    parser.add_argument("--upload-base-dir", default=None)
    args = parser.parse_args()

    upload_base_dir = _resolve_upload_base_dir(args.upload_base_dir)
    original_path = _find_original_path(args.file_id, upload_base_dir)
    headers, original_rows = _read_csv(original_path)
    harmonized_rows = [list(row) for row in original_rows]
    changes = _parse_changes(args.changes)

    for row_idx, column_changes in changes.items():
        if row_idx < len(harmonized_rows):
            for column_key, value in column_changes.items():
                column_id = _resolve_column_id(headers, column_key)
                if column_id is None:
                    continue
                while len(harmonized_rows[row_idx]) <= column_id:
                    harmonized_rows[row_idx].append("")
                harmonized_rows[row_idx][column_id] = value

    _write_harmonized(original_path, headers, harmonized_rows)

    if not args.no_manifest:
        manifest_rows = _build_manifest_rows(headers, original_rows, harmonized_rows, changes, args.file_id)
        _write_manifest(args.file_id, manifest_rows, upload_base_dir)


if __name__ == "__main__":
    main()
