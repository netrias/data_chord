#!/usr/bin/env python3
"""Load one approved reference-data file into a portable SQLite store."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from src.integrations.reference_data_file import load_reference_models
from src.integrations.sqlite_reference_data import (
    SqliteReferenceDataImporter,
    SqliteReferenceDataRepository,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    load_sqlite = commands.add_parser(
        "load-sqlite",
        help="Load one approved canonical file into a portable SQLite database",
    )
    load_sqlite.add_argument("--input", type=Path, required=True)
    load_sqlite.add_argument("--expected-sha256", required=True)
    load_sqlite.add_argument("--database", type=Path, required=True)
    load_sqlite.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace changed content for an existing model version",
    )
    args = parser.parse_args()
    _load_sqlite(
        args.input,
        args.expected_sha256,
        args.database,
        replace_existing=args.replace_existing,
    )


def _load_sqlite(
    input_path: Path,
    expected_sha256: str,
    database: Path,
    *,
    replace_existing: bool = False,
) -> None:
    _require_source_digest(input_path, expected_sha256)
    models = load_reference_models(input_path)
    if not models:
        raise RuntimeError("Reference file contains no model versions")
    SqliteReferenceDataImporter(database).import_models(
        models,
        replace_existing=replace_existing,
    )
    repository = SqliteReferenceDataRepository(database)
    for model in models:
        if repository.load_model(model.version) != model:
            raise RuntimeError(f"Reference model did not verify: {model.version}")
    print(f"Loaded and verified {len(models)} model versions in {database}")


def _require_source_digest(input_path: Path, expected_sha256: str) -> str:
    actual_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(f"Reference file SHA-256 mismatch: expected {expected_sha256}, found {actual_sha256}")
    return actual_sha256


if __name__ == "__main__":
    main()
