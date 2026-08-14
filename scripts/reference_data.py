#!/usr/bin/env python3
"""Export live standards once, then import the recovery file into DynamoDB."""

from __future__ import annotations

import argparse
import asyncio
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Export canonical JSON from the old service")
    export.add_argument("--environment", choices=["staging", "prod"], required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--api-key", default=os.getenv("NETRIAS_API_KEY"))
    import_command = commands.add_parser("import", help="Import canonical JSON into DynamoDB")
    import_command.add_argument("--input", type=Path, required=True)
    import_command.add_argument("--table", required=True)
    import_command.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-2"))
    import_command.add_argument("--expected-model-count", type=int, required=True)
    verify = commands.add_parser("verify", help="Verify the complete published DynamoDB catalog")
    verify.add_argument("--table", required=True)
    verify.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-2"))
    args = parser.parse_args()
    if args.command == "export":
        if not args.api_key:
            parser.error("export requires --api-key or NETRIAS_API_KEY")
        asyncio.run(_export(args.environment, args.api_key, args.output))
        return
    if args.command == "import":
        _import(args.input, args.table, args.region, args.expected_model_count)
        return
    _verify(args.table, args.region)


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


def _import(input_path: Path, table_name: str, region: str, expected_model_count: int) -> None:
    resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=region))
    importer = ReferenceDataImporter(resource.Table(table_name))
    models = load_reference_models(input_path)
    if len(models) != expected_model_count:
        raise RuntimeError(f"Expected {expected_model_count} model versions, found {len(models)}")
    importer.import_models(models)
    print(f"Imported and verified {len(models)} model versions in {table_name}")


def _verify(table_name: str, region: str) -> None:
    resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=region))
    repository = DynamoDbReferenceDataRepository(resource.Table(table_name))
    summaries = repository.list_models()
    versions = [
        DataModelVersionReference(summary.data_model_key, version.external_version_number)
        for summary in summaries
        for version in summary.versions
    ]
    for version in versions:
        repository.load_model(version)
    if not versions:
        raise RuntimeError("Reference catalog is empty")
    print(f"Verified {len(versions)} complete model versions in {table_name}")


if __name__ == "__main__":
    main()
