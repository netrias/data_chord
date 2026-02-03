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

from src.domain.dependencies import UPLOAD_BASE_DIR
from src.domain.manifest import get_manifest_schema


def _find_original_path(file_id: str) -> Path:
    files_dir = UPLOAD_BASE_DIR / "files"
    matches = sorted(files_dir.glob(f"{file_id}.*"))
    if not matches:
        raise FileNotFoundError(f"No uploaded file found for {file_id}")
    return matches[0]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = list(reader.fieldnames or [])
    return headers, rows


def _write_harmonized(path: Path, headers: list[str], rows: list[dict[str, str]]) -> Path:
    harmonized_path = path.with_name(f"{path.stem}.harmonized.csv")
    with harmonized_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return harmonized_path


def _build_manifest_rows(
    headers: list[str],
    original_rows: list[dict[str, str]],
    harmonized_rows: list[dict[str, str]],
    changes: dict[int, dict[str, str]],
    file_id: str,
) -> list[dict[str, Any]]:
    columns_with_changes = {col for row in changes.values() for col in row.keys()}
    if not columns_with_changes:
        columns_with_changes = set(headers[:2]) if len(headers) >= 2 else set(headers)

    manifest_rows: list[dict[str, Any]] = []
    for col_name in columns_with_changes:
        for row_idx, original_row in enumerate(original_rows):
            original_value = original_row.get(col_name, "")
            harmonized_value = harmonized_rows[row_idx].get(col_name, original_value)
            manifest_rows.append({
                "job_id": f"e2e-job-{file_id}",
                "column_id": headers.index(col_name) if col_name in headers else 0,
                "column_name": col_name,
                "to_harmonize": original_value,
                "top_harmonization": harmonized_value,
                "ontology_id": None,
                "top_harmonizations": [harmonized_value] if harmonized_value else [],
                "confidence_score": 0.95 if original_value != harmonized_value else 0.99,
                "error": None,
                "row_indices": [row_idx],
                "manual_overrides": [],
            })
    return manifest_rows


def _write_manifest(file_id: str, manifest_rows: list[dict[str, Any]]) -> Path:
    manifest_dir = UPLOAD_BASE_DIR / "manifests"
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--changes", default=None)
    parser.add_argument("--no-manifest", action="store_true")
    args = parser.parse_args()

    original_path = _find_original_path(args.file_id)
    headers, original_rows = _read_csv(original_path)
    harmonized_rows = [row.copy() for row in original_rows]
    changes = _parse_changes(args.changes)

    for row_idx, column_changes in changes.items():
        if row_idx < len(harmonized_rows):
            harmonized_rows[row_idx].update(column_changes)

    _write_harmonized(original_path, headers, harmonized_rows)

    if not args.no_manifest:
        manifest_rows = _build_manifest_rows(headers, original_rows, harmonized_rows, changes, args.file_id)
        _write_manifest(args.file_id, manifest_rows)


if __name__ == "__main__":
    main()
