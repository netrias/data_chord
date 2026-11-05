#!/usr/bin/env python3
"""Export reference data or load one approved file into a runtime store."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from pathlib import Path
from typing import cast

import boto3
from dotenv import dotenv_values
from netrias_client import NetriasClient

from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_placements import PlacedReferenceModel
from src.integrations.data_model_store import (
    fetch_all_pvs_async,
    fetch_nodes_and_placements_async,
    list_data_model_summaries,
)
from src.integrations.dynamodb_reference_data import (
    DynamoDbReferenceDataRepository,
    DynamoResource,
    ReferenceDataImporter,
)
from src.integrations.reference_data_file import (
    load_placed_reference_models,
    load_reference_models,
    save_placed_reference_models,
)
from src.integrations.sqlite_reference_data import (
    SqliteReferenceDataImporter,
    SqliteReferenceDataRepository,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Export canonical JSON from the configured Data Model Store")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    export.add_argument("--dms-url", help="Data Model Store API base URL")
    export.add_argument("--api-key", help="Data Model Store API key")
    sync = commands.add_parser("sync", help="Synchronize one approved canonical file")
    sync.add_argument("--input", type=Path, required=True)
    sync.add_argument("--expected-sha256", required=True)
    sync.add_argument("--table", required=True)
    sync.add_argument("--region", required=True)
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
    if args.command == "export":
        file_values = dotenv_values(args.env_file) if args.env_file.is_file() else {}
        dms_url = args.dms_url or os.getenv("DATA_CHORD_DMS_URL") or file_values.get("DATA_CHORD_DMS_URL")
        api_key = args.api_key or os.getenv("DATA_CHORD_DMS_API_KEY") or file_values.get("DATA_CHORD_DMS_API_KEY")
        if not dms_url or not api_key:
            parser.error(
                "export requires DATA_CHORD_DMS_URL and DATA_CHORD_DMS_API_KEY "
                "(flags, environment, or --env-file)"
            )
        asyncio.run(_export(dms_url, api_key, args.output))
        return
    if args.command == "sync":
        _sync(args.input, args.expected_sha256, args.table, args.region)
        return
    _load_sqlite(
        args.input,
        args.expected_sha256,
        args.database,
        replace_existing=args.replace_existing,
    )


async def _export(dms_url: str, api_key: str, output: Path) -> None:
    client = NetriasClient(api_key=api_key)
    client.configure(data_model_store_url=dms_url)
    models: list[PlacedReferenceModel] = []
    for summary in list_data_model_summaries(client):
        for version_info in summary.versions:
            version = DataModelVersionReference(summary.data_model_key, version_info.external_version_number)
            nodes, placements = await fetch_nodes_and_placements_async(
                client, version.data_model_key, version.external_version_number
            )
            pvs = (
                await fetch_all_pvs_async(client, version.data_model_key, version.external_version_number)
            ).with_defaults(placement.cde_key for placement in placements)
            models.append(
                PlacedReferenceModel(
                    version=version,
                    label=summary.label,
                    nodes=nodes,
                    placements=placements,
                    values=pvs.values,
                )
            )
    save_placed_reference_models(output, models)
    print(f"Exported {len(models)} model versions to {output}")


def _sync(input_path: Path, expected_sha256: str, table_name: str, region: str) -> None:
    actual_sha256 = _require_source_digest(input_path, expected_sha256)
    models = load_placed_reference_models(input_path)
    if not models:
        raise RuntimeError("Reference file contains no model versions")
    resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=region))
    table = resource.Table(table_name)
    ReferenceDataImporter(table).import_models(models, source_digest=actual_sha256)
    repository = DynamoDbReferenceDataRepository(table)
    for model in models:
        if repository.load_placements(model.version) != model:
            raise RuntimeError(f"Reference model did not verify: {model.version}")
    published_count = sum(len(summary.versions) for summary in repository.list_models())
    if published_count != len(models):
        raise RuntimeError(
            f"Reference catalog has {published_count} model versions; expected {len(models)}"
        )
    print(f"Synchronized and verified {len(models)} model versions in {table_name}")


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
        raise RuntimeError(
            f"Reference file SHA-256 mismatch: expected {expected_sha256}, found {actual_sha256}"
        )
    return actual_sha256


if __name__ == "__main__":
    main()
