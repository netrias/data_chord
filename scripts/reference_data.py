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
from netrias_client import Environment, NetriasClient

from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_type_classification import refine_cde_types_from_pvs
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel
from src.integrations.data_model_store import fetch_all_pvs_async, fetch_cdes, list_data_model_summaries
from src.integrations.dynamodb_reference_data import (
    DynamoDbReferenceDataRepository,
    DynamoResource,
    ReferenceDataImporter,
)
from src.integrations.reference_data_file import load_reference_models, save_reference_models
from src.integrations.sqlite_reference_data import (
    SqliteReferenceDataImporter,
    SqliteReferenceDataRepository,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Export canonical JSON from the legacy service")
    export.add_argument("--environment", choices=["staging", "prod"], required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--api-key", default=os.getenv("NETRIAS_API_KEY"))
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
    args = parser.parse_args()
    if args.command == "export":
        if not args.api_key:
            parser.error("export requires --api-key or NETRIAS_API_KEY")
        asyncio.run(_export(args.environment, args.api_key, args.output))
        return
    if args.command == "sync":
        _sync(args.input, args.expected_sha256, args.table, args.region)
        return
    _load_sqlite(args.input, args.expected_sha256, args.database)


async def _export(environment: str, api_key: str, output: Path) -> None:
    client = NetriasClient(api_key=api_key, environment=Environment(environment))
    models: list[ReferenceModel] = []
    for summary in list_data_model_summaries(client):
        for version_info in summary.versions:
            version = DataModelVersionReference(summary.data_model_key, version_info.external_version_number)
            catalog = CdeCatalog.from_cdes(fetch_cdes(client, version.data_model_key, version.external_version_number))
            pvs = (
                await fetch_all_pvs_async(client, version.data_model_key, version.external_version_number)
            ).with_defaults(catalog.keys())
            models.append(
                ReferenceModel(
                    version=version,
                    label=summary.label,
                    catalog=refine_cde_types_from_pvs(catalog, pvs),
                    pvs=pvs,
                )
            )
    save_reference_models(output, models)
    print(f"Exported {len(models)} model versions to {output}")


def _sync(input_path: Path, expected_sha256: str, table_name: str, region: str) -> None:
    actual_sha256 = _require_source_digest(input_path, expected_sha256)
    models = load_reference_models(input_path)
    if not models:
        raise RuntimeError("Reference file contains no model versions")
    resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=region))
    table = resource.Table(table_name)
    ReferenceDataImporter(table).import_models(models, source_digest=actual_sha256)
    repository = DynamoDbReferenceDataRepository(table)
    for model in models:
        if repository.load_model(model.version) != model:
            raise RuntimeError(f"Reference model did not verify: {model.version}")
    published_count = sum(len(summary.versions) for summary in repository.list_models())
    if published_count != len(models):
        raise RuntimeError(
            f"Reference catalog has {published_count} model versions; expected {len(models)}"
        )
    print(f"Synchronized and verified {len(models)} model versions in {table_name}")


def _load_sqlite(input_path: Path, expected_sha256: str, database: Path) -> None:
    _require_source_digest(input_path, expected_sha256)
    models = load_reference_models(input_path)
    if not models:
        raise RuntimeError("Reference file contains no model versions")
    SqliteReferenceDataImporter(database).import_models(models)
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
