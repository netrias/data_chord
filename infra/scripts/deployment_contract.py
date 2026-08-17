#!/usr/bin/env python3
"""Validate and read one generated DataChord deployment contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_FIELDS = {
    "application_commit",
    "application_repository_url",
    "application_role_boundary_arn",
    "application_role_path",
    "aws_partition",
    "aws_region",
    "deployer_role_arn",
    "domain_label",
    "expected_account_id",
    "github_app_secret_name",
    "hosted_zone_name",
    "netrias_api_key_secret_name",
    "stage",
    "state_bucket_name",
    "state_key",
    "target_slug",
}
_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")
_ACCOUNT = re.compile(r"^[0-9]{12}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REGION = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]$")
_REPOSITORY = re.compile(r"^https://github\.com/[^/]+/[^/]+\.git$")
_STAGES = {"dev", "qa", "staging", "prod"}


class ContractError(ValueError):
    """The generated deployment contract is unsafe or malformed."""


def _main(arguments: list[str]) -> int:
    try:
        if len(arguments) < 2:
            raise ContractError(
                "usage: deployment_contract.py validate FILE TARGET STAGE | get FILE KEY"
            )
        action = arguments[0]
        path = Path(arguments[1])
        document = _load(path)
        if action == "validate" and len(arguments) == 4:
            _validate_selection(document, arguments[2], arguments[3])
            return 0
        if action == "get" and len(arguments) == 3:
            print(_string(document, arguments[2]))
            return 0
        raise ContractError(
            "usage: deployment_contract.py validate FILE TARGET STAGE | get FILE KEY"
        )
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"deployment contract does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in deployment contract: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("deployment contract must contain a JSON object")
    document = {str(key): item for key, item in value.items()}
    extra = sorted(set(document) - _FIELDS)
    missing = sorted(_FIELDS - set(document))
    if extra or missing:
        parts: list[str] = []
        if extra:
            parts.append(f"unsupported fields: {', '.join(extra)}")
        if missing:
            parts.append(f"missing fields: {', '.join(missing)}")
        raise ContractError("; ".join(parts))
    _validate(document)
    return document


def _validate(document: dict[str, object]) -> None:
    target = _string(document, "target_slug")
    stage = _string(document, "stage")
    account = _string(document, "expected_account_id")
    partition = _string(document, "aws_partition")
    region = _string(document, "aws_region")
    commit = _string(document, "application_commit")
    if not _SLUG.fullmatch(target):
        raise ContractError(f"invalid target slug: {target!r}")
    if stage not in _STAGES:
        raise ContractError(f"invalid stage: {stage!r}")
    if not _ACCOUNT.fullmatch(account):
        raise ContractError(f"invalid AWS account id: {account!r}")
    if not _REGION.fullmatch(region):
        raise ContractError(f"invalid AWS region: {region!r}")
    if partition != "aws" or region.startswith("us-gov-"):
        raise ContractError("only the standard AWS partition is supported")
    if not _COMMIT.fullmatch(commit):
        raise ContractError("application_commit must be a full 40-character Git SHA")
    if not _REPOSITORY.fullmatch(_string(document, "application_repository_url")):
        raise ContractError("application_repository_url must be an HTTPS GitHub repository")
    _validate_iam(document, partition, account)
    _validate_state_key(document, target, stage)


def _validate_iam(document: dict[str, object], partition: str, account: str) -> None:
    arn_prefix = f"arn:{partition}:iam::{account}:"
    expected_role = f"{arn_prefix}role/foundation/datachord-deployer"
    if _string(document, "deployer_role_arn") != expected_role:
        raise ContractError("deployer_role_arn does not identify the deployment role")
    expected_boundary = f"{arn_prefix}policy/datachord-application-role-boundary"
    if _string(document, "application_role_boundary_arn") != expected_boundary:
        raise ContractError("application_role_boundary_arn does not identify the application boundary")
    if _string(document, "application_role_path") != "/application/":
        raise ContractError("application_role_path must be /application/")


def _validate_state_key(document: dict[str, object], target: str, stage: str) -> None:
    expected_state_key = (
        "data-chord/prod/tofu.tfstate"
        if target == "bdf" and stage == "prod"
        else f"datachord/{target}/{stage}/tofu.tfstate"
    )
    if _string(document, "state_key") != expected_state_key:
        raise ContractError(f"state_key must be {expected_state_key!r}")


def _validate_selection(
    document: dict[str, object],
    selected_target: str,
    selected_stage: str,
) -> None:
    target = _string(document, "target_slug")
    stage = _string(document, "stage")
    if target != selected_target:
        raise ContractError(
            f"deployment contract selects target {target!r}, not {selected_target!r}"
        )
    if stage != selected_stage:
        raise ContractError(
            f"deployment contract selects stage {stage!r}, not {selected_stage!r}"
        )


def _string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{key} must be a non-empty string")
    return value


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
