#!/usr/bin/env python3
"""Create and verify the bounded forecast used by DataChord deploy."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from environment import (
    DeploymentRoot,
    Environment,
    EnvironmentError,
    canonical_digest,
    load_selected_environment,
)

RECEIPT_SCHEMA_VERSION = 3
_RECEIPT_FIELDS = {
    "account_id",
    "commit",
    "config_digest",
    "deployment_root",
    "forecast",
    "partition",
    "region",
    "repository_url",
    "schema_version",
    "stage",
    "state",
    "state_bucket_name",
    "state_key",
    "status",
    "target",
}
_PREREQUISITE_ADDRESSES = {
    "data.aws_caller_identity.current",
    "data.aws_partition.current",
    "data.aws_secretsmanager_secret.github_app",
    "aws_cloudwatch_log_group.codebuild",
    "aws_codebuild_project.app_image",
    "aws_ecr_repository.app",
    "aws_iam_role.application_build",
    "aws_iam_role_policy.application_build",
    "module.data_plane.aws_s3_bucket.workflow",
}
_SAFE_REPLACEMENTS = {
    "aws_ecs_task_definition.application": ["delete", "create"],
}
_CUSTOMER_PLATFORM_ADDRESSES = {
    "data.aws_caller_identity.current",
    "data.aws_partition.current",
    "module.data_plane.aws_dynamodb_table.cde_recommendation_cache",
    "module.data_plane.aws_dynamodb_table.harmonization_cache",
    "module.data_plane.aws_dynamodb_table.reference_data",
    "module.data_plane.aws_s3_bucket.workflow",
    "module.data_plane.aws_s3_bucket_public_access_block.workflow",
}


class ReceiptError(ValueError):
    """The deployment receipt or an internal plan is no longer safe."""


@dataclass(frozen=True)
class StateIdentity:
    lineage: str | None
    serial: int | None

    def document(self) -> dict[str, object]:
        if self.lineage is None:
            return {"kind": "absent"}
        return {"kind": "present", "lineage": self.lineage, "serial": self.serial}


def create_receipt(
    receipt_path: Path,
    environment_path: Path,
    target: str,
    stage: str,
    deployment_root: DeploymentRoot,
    commit: str,
    state_path: Path | None,
    plan_json_path: Path,
) -> None:
    environment = load_selected_environment(environment_path, target, stage, deployment_root)
    forecast = _forecast(plan_json_path)
    _reject_destructive_changes(forecast)
    if deployment_root is DeploymentRoot.CUSTOMER_PLATFORM:
        _require_customer_platform_forecast(forecast)
    document: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "planned",
        "target": target,
        "stage": stage,
        "deployment_root": deployment_root.value,
        "config_digest": canonical_digest(environment),
        "commit": _commit(commit),
        "account_id": environment.account_id,
        "partition": environment.partition,
        "region": environment.region,
        "state_bucket_name": environment.state_bucket_name,
        "state_key": environment.state_key,
        "repository_url": _repository_url(environment),
        "state": _state_identity(state_path).document(),
        "forecast": forecast,
    }
    _atomic_write(receipt_path, document)


def validate_receipt(
    receipt_path: Path,
    environment_path: Path,
    target: str,
    stage: str,
    deployment_root: DeploymentRoot,
    commit: str,
    state_path: Path | None,
    expected_status: str,
) -> None:
    document = _load_receipt(receipt_path)
    environment = load_selected_environment(environment_path, target, stage, deployment_root)
    expected: dict[str, object] = {
        "status": expected_status,
        "target": target,
        "stage": stage,
        "deployment_root": deployment_root.value,
        "config_digest": canonical_digest(environment),
        "commit": _commit(commit),
        "account_id": environment.account_id,
        "partition": environment.partition,
        "region": environment.region,
        "state_bucket_name": environment.state_bucket_name,
        "state_key": environment.state_key,
        "repository_url": _repository_url(environment),
        "state": _state_identity(state_path).document(),
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise ReceiptError(f"deployment receipt no longer matches {key}; run plan again")


def check_internal_plan(receipt_path: Path, plan_json_path: Path, phase: str) -> None:
    document = _load_receipt(receipt_path)
    if document.get("status") != "in_progress":
        raise ReceiptError("deployment receipt is not in progress")
    approved = _forecast_mapping(document.get("forecast"))
    actual = _forecast(plan_json_path)
    _reject_destructive_changes(actual)
    for change in actual:
        address = cast(str, change["address"])
        if phase == "prerequisite" and address not in _PREREQUISITE_ADDRESSES:
            raise ReceiptError(f"prerequisite plan contains unexpected resource: {address}")
        if approved.get(address) != change["actions"]:
            raise ReceiptError(f"internal plan was not in the approved forecast: {address}")


def set_status(receipt_path: Path, expected: str, replacement: str) -> None:
    document = _load_receipt(receipt_path)
    if document.get("status") != expected:
        raise ReceiptError(f"deployment receipt status must be {expected}")
    document["status"] = replacement
    _atomic_write(receipt_path, document)


def invalidate_receipt(receipt_path: Path) -> None:
    if not receipt_path.exists():
        return
    try:
        document = _load_receipt(receipt_path)
    except ReceiptError:
        document: dict[str, object] = {field: None for field in _RECEIPT_FIELDS}
        document["schema_version"] = RECEIPT_SCHEMA_VERSION
    document["status"] = "invalid"
    _atomic_write(receipt_path, document)


def _load_receipt(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReceiptError(f"deployment receipt does not exist: {path}; run plan first") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"deployment receipt is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise ReceiptError("deployment receipt must contain one JSON object")
    document = {str(key): value for key, value in raw.items()}
    if set(document) != _RECEIPT_FIELDS:
        raise ReceiptError("deployment receipt has an unsupported shape; run plan again")
    if document.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ReceiptError("deployment receipt version is unsupported; run plan again")
    _forecast_mapping(document.get("forecast"))
    return document


def _forecast(path: Path) -> list[dict[str, object]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"OpenTofu plan JSON is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise ReceiptError("OpenTofu plan JSON must contain one object")
    raw_changes = raw.get("resource_changes") or []
    if not isinstance(raw_changes, list):
        raise ReceiptError("OpenTofu resource changes must be a list")
    changes: list[dict[str, object]] = []
    for raw_change in raw_changes:
        if not isinstance(raw_change, dict) or not isinstance(raw_change.get("address"), str):
            raise ReceiptError("OpenTofu resource change is invalid")
        change = raw_change.get("change")
        if not isinstance(change, dict) or not isinstance(change.get("actions"), list):
            raise ReceiptError("OpenTofu resource actions are invalid")
        actions = change["actions"]
        if actions == ["no-op"]:
            continue
        if not actions or any(not isinstance(action, str) for action in actions):
            raise ReceiptError("OpenTofu resource actions are invalid")
        changes.append({"address": raw_change["address"], "actions": actions})
    return sorted(changes, key=lambda item: cast(str, item["address"]))


def _forecast_mapping(raw: object) -> dict[str, object]:
    if not isinstance(raw, list):
        raise ReceiptError("deployment forecast must be a list")
    result: dict[str, object] = {}
    for item in raw:
        if (
            not isinstance(item, dict)
            or set(item) != {"address", "actions"}
            or not isinstance(item.get("address"), str)
            or not isinstance(item.get("actions"), list)
        ):
            raise ReceiptError("deployment forecast is invalid")
        address = cast(str, item["address"])
        if address in result:
            raise ReceiptError(f"deployment forecast repeats resource: {address}")
        result[address] = item["actions"]
    return result


def _reject_destructive_changes(changes: list[dict[str, object]]) -> None:
    for change in changes:
        actions = cast(list[str], change["actions"])
        address = cast(str, change["address"])
        if "delete" in actions and _SAFE_REPLACEMENTS.get(address) != actions:
            raise ReceiptError(f"deployment would delete or replace {address}; destructive deploys are not supported")


def _state_identity(path: Path | None) -> StateIdentity:
    if path is None or not path.exists():
        return StateIdentity(None, None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"OpenTofu state is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise ReceiptError("OpenTofu state must contain one JSON object")
    lineage, serial = raw.get("lineage"), raw.get("serial")
    if lineage == "" and serial == 0 and raw.get("outputs") == {} and raw.get("resources") == []:
        return StateIdentity(None, None)
    if not isinstance(lineage, str) or not lineage or not isinstance(serial, int):
        raise ReceiptError("OpenTofu state has no valid lineage and serial")
    return StateIdentity(lineage, serial)


def _commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ReceiptError("Git commit must be 40 lowercase hexadecimal characters")
    return value


def _atomic_write(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _optional_path(value: str) -> Path | None:
    return None if value == "-" else Path(value)


def _repository_url(environment: object) -> str | None:
    if isinstance(environment, Environment):
        return environment.application_repository_url
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("create", "validate"):
        command = commands.add_parser(action)
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--environment", type=Path, required=True)
        command.add_argument("--target", required=True)
        command.add_argument("--stage", required=True)
        command.add_argument(
            "--deployment-root",
            type=DeploymentRoot,
            choices=list(DeploymentRoot),
            required=True,
        )
        command.add_argument("--commit", required=True)
        command.add_argument("--state", required=True)
        if action == "create":
            command.add_argument("--plan-json", type=Path, required=True)
        else:
            command.add_argument(
                "--expected-status",
                choices=["planned", "in_progress"],
                default="planned",
            )
    check = commands.add_parser("check-plan")
    check.add_argument("--receipt", type=Path, required=True)
    check.add_argument("--plan-json", type=Path, required=True)
    check.add_argument("--phase", choices=["prerequisite", "application"], required=True)
    status = commands.add_parser("status")
    status.add_argument("--receipt", type=Path, required=True)
    status.add_argument("--from-status", required=True)
    status.add_argument("--to-status", required=True)
    invalidate = commands.add_parser("invalidate")
    invalidate.add_argument("--receipt", type=Path, required=True)
    return parser


def _require_customer_platform_forecast(
    changes: list[dict[str, object]],
) -> None:
    for change in changes:
        address = cast(str, change["address"])
        if address not in _CUSTOMER_PLATFORM_ADDRESSES:
            raise ReceiptError(f"customer-platform plan contains unexpected resource: {address}")


def _main(arguments: list[str]) -> int:
    try:
        args = _parser().parse_args(arguments)
        if args.action == "create":
            create_receipt(
                args.receipt,
                args.environment,
                args.target,
                args.stage,
                args.deployment_root,
                args.commit,
                _optional_path(args.state),
                args.plan_json,
            )
        elif args.action == "validate":
            validate_receipt(
                args.receipt,
                args.environment,
                args.target,
                args.stage,
                args.deployment_root,
                args.commit,
                _optional_path(args.state),
                args.expected_status,
            )
        elif args.action == "check-plan":
            check_internal_plan(args.receipt, args.plan_json, args.phase)
        elif args.action == "status":
            set_status(args.receipt, args.from_status, args.to_status)
        elif args.action == "invalidate":
            invalidate_receipt(args.receipt)
        return 0
    except (EnvironmentError, ReceiptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
