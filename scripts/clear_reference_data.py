#!/usr/bin/env python3
"""Preview or delete items in one Data Chord reference-data DynamoDB table."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator
from typing import Any

import boto3

TABLE_NAME = re.compile(r"data-chord-(?:dev|qa|staging|prod)-reference-data\Z")
KEY_NAMES = {"pk", "sk"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, help="Exact Data Chord reference-data table name")
    parser.add_argument("--region", required=True, help="AWS region containing the table")
    parser.add_argument("--expected-account-id", required=True, help="12-digit AWS account ID")
    parser.add_argument("--profile", help="AWS profile; defaults to the usual boto3 credential chain")
    parser.add_argument("--execute", action="store_true", help="Delete the table's items after validation")
    parser.add_argument("--confirm-table", help="Repeat the exact table name when using --execute")
    args = parser.parse_args()

    try:
        count = clear_reference_data(
            table_name=args.table,
            region=args.region,
            expected_account_id=args.expected_account_id,
            profile=args.profile,
            execute=args.execute,
            confirm_table=args.confirm_table,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.execute:
        print(f"Deleted {count} items from {args.table}; verified the table is empty.")
    else:
        print(f"Preview: {count} items in {args.table}. No items were deleted.")


def clear_reference_data(
    *,
    table_name: str,
    region: str,
    expected_account_id: str,
    profile: str | None = None,
    execute: bool = False,
    confirm_table: str | None = None,
) -> int:
    """Empty only the explicitly named Data Chord reference-data table."""
    if TABLE_NAME.fullmatch(table_name) is None:
        raise ValueError("--table must be data-chord-{dev,qa,staging,prod}-reference-data")
    if re.fullmatch(r"\d{12}", expected_account_id) is None:
        raise ValueError("--expected-account-id must be a 12-digit AWS account ID")
    if execute and confirm_table != table_name:
        raise ValueError("--execute requires --confirm-table with the exact --table value")
    if not execute and confirm_table is not None:
        raise ValueError("--confirm-table is only valid with --execute")

    session = boto3.Session(profile_name=profile, region_name=region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    if account_id != expected_account_id:
        raise ValueError(f"AWS account is {account_id}; expected {expected_account_id}")

    client = session.client("dynamodb")
    description = client.describe_table(TableName=table_name)["Table"]
    _validate_table_identity(description, table_name, region, expected_account_id)

    table = session.resource("dynamodb").Table(table_name)
    keys = [key for page in _key_pages(table) for key in page]
    if execute and keys:
        with table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key=key)

    if execute and any(page for page in _key_pages(table)):
        raise RuntimeError("Reference-data table still contains items after deletion")
    return len(keys)


def _validate_table_identity(
    description: dict[str, Any], table_name: str, region: str, expected_account_id: str
) -> None:
    arn = description["TableArn"].split(":", 5)
    if (
        len(arn) != 6
        or arn[0] != "arn"
        or arn[1] not in {"aws", "aws-us-gov", "aws-cn"}
        or arn[2] != "dynamodb"
        or arn[3] != region
        or arn[4] != expected_account_id
        or arn[5] != f"table/{table_name}"
        or description["TableName"] != table_name
        or {entry["AttributeName"] for entry in description["KeySchema"]} != KEY_NAMES
    ):
        raise ValueError("AWS table ARN, name, region, account, or key schema does not match")


def _key_pages(table: Any) -> Iterator[list[dict[str, Any]]]:
    scan_args: dict[str, Any] = {
        "ProjectionExpression": "#pk, #sk",
        "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
        "ConsistentRead": True,
    }
    while True:
        result = table.scan(**scan_args)
        keys = result["Items"]
        if any(set(key) != KEY_NAMES for key in keys):
            raise RuntimeError("Scan did not return the expected pk/sk keys")
        yield keys
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            return
        scan_args["ExclusiveStartKey"] = last_key


if __name__ == "__main__":
    main()
