"""Seed harmonized tabular output and manifest parquet for Playwright E2E tests."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from netrias_client import TabularDataset, dataset_from_rows, read_tabular, write_tabular

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# noqa: E402 - ensure repo root is on sys.path before importing application modules.
from src.domain.manifest import get_manifest_schema  # noqa: E402
from src.domain.storage import UploadConstraints, UploadStorage  # noqa: E402


def _resolve_upload_base_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    from src.domain.dependencies import UPLOAD_BASE_DIR

    return UPLOAD_BASE_DIR


def _upload_storage_for(upload_base_dir: Path) -> UploadStorage:
    from src.domain.dependencies import MAX_UPLOAD_BYTES

    return UploadStorage(upload_base_dir, UploadConstraints(max_bytes=MAX_UPLOAD_BYTES))


def _write_harmonized(
    file_id: str,
    original_path: Path,
    dataset: TabularDataset,
    storage: UploadStorage,
) -> Path:
    harmonized_path = storage.harmonized_path_for(file_id, original_path)
    template_path = original_path if dataset.source_format.value == "xlsx" else None
    write_tabular(harmonized_path, dataset, template_path=template_path)
    return harmonized_path


def _build_manifest_rows(
    original_dataset: TabularDataset,
    harmonized_dataset: TabularDataset,
    changes: dict[int, dict[str, str]],
    file_id: str,
) -> list[dict[str, Any]]:
    headers = original_dataset.headers
    header_by_index = {column.header: column for column in original_dataset.columns}
    columns_with_changes = {col for row in changes.values() for col in row}
    if not columns_with_changes:
        columns_with_changes = set(headers[:2]) if len(headers) >= 2 else set(headers)

    manifest_rows: list[dict[str, Any]] = []
    for col_name in columns_with_changes:
        column = header_by_index.get(col_name)
        if column is None:
            continue
        grouped: dict[str, dict[str, Any]] = {}
        for row_idx, original_row in enumerate(original_dataset.rows):
            original_value = original_row[column.index] if column.index < len(original_row) else ""
            harmonized_row = harmonized_dataset.rows[row_idx] if row_idx < len(harmonized_dataset.rows) else []
            harmonized_value = harmonized_row[column.index] if column.index < len(harmonized_row) else original_value
            if original_value not in grouped:
                grouped[original_value] = {
                    "job_id": f"e2e-job-{file_id}",
                    "column_id": column.index,
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


def _write_manifest(file_id: str, manifest_rows: list[dict[str, Any]], storage: UploadStorage) -> Path:
    schema = get_manifest_schema()
    with tempfile.TemporaryDirectory() as temp_dir:
        manifest_path = Path(temp_dir) / f"{file_id}_harmonization.parquet"
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
        return storage.save_harmonization_manifest(file_id, manifest_path)


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
    parser.add_argument("--upload-base-dir", default=None)
    args = parser.parse_args()

    upload_base_dir = _resolve_upload_base_dir(args.upload_base_dir)
    storage = _upload_storage_for(upload_base_dir)
    meta = storage.load(args.file_id)
    if meta is None:
        raise FileNotFoundError(f"No uploaded file found for {args.file_id}")
    original_dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)
    harmonized_rows = [row.copy() for row in original_dataset.rows]
    changes = _parse_changes(args.changes)
    header_by_index = {column.header: column for column in original_dataset.columns}

    for row_idx, column_changes in changes.items():
        if row_idx < len(harmonized_rows):
            for header, value in column_changes.items():
                column = header_by_index.get(header)
                if column is not None and column.index < len(harmonized_rows[row_idx]):
                    harmonized_rows[row_idx][column.index] = value

    harmonized_dataset = dataset_from_rows(
        columns=original_dataset.columns,
        rows=harmonized_rows,
        source_format=original_dataset.source_format,
        sheet_name=original_dataset.sheet_name,
    )

    _write_harmonized(args.file_id, meta.saved_path, harmonized_dataset, storage)

    if not args.no_manifest:
        manifest_rows = _build_manifest_rows(original_dataset, harmonized_dataset, changes, args.file_id)
        _write_manifest(args.file_id, manifest_rows, storage)


if __name__ == "__main__":
    main()
