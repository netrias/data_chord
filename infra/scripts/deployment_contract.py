#!/usr/bin/env python3
"""Validate and read one generated DataChord deployment contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

_FIELDS = {
    "contract_version",
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
_STAGES = {"dev", "qa", "staging", "prod"}
CONTRACT_VERSION = 1


class ContractError(ValueError):
    """The generated deployment contract is unsafe or malformed."""


def _main(arguments: list[str]) -> int:
    try:
        if len(arguments) < 2:
            raise ContractError("usage: deployment_contract.py validate FILE TARGET STAGE | get FILE KEY")
        action, path = arguments[0], Path(arguments[1])
        document = _load(path)
        if action == "validate" and len(arguments) == 4:
            _validate_selection(document, arguments[2], arguments[3])
            return 0
        if action == "get" and len(arguments) == 3:
            print(_string(document, arguments[2]))
            return 0
        raise ContractError("usage: deployment_contract.py validate FILE TARGET STAGE | get FILE KEY")
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
    extra, missing = sorted(set(document) - _FIELDS), sorted(_FIELDS - set(document))
    if extra or missing:
        parts = []
        if extra:
            parts.append(f"unsupported fields: {', '.join(extra)}")
        if missing:
            parts.append(f"missing fields: {', '.join(missing)}")
        raise ContractError("; ".join(parts))
    _validate(document)
    return document


def _validate(document: dict[str, object]) -> None:
    if document.get("contract_version") != CONTRACT_VERSION:
        raise ContractError(f"contract_version must be {CONTRACT_VERSION}")
    target, stage = _string(document, "target_slug"), _string(document, "stage")
    account = _string(document, "expected_account_id")
    partition, region = _string(document, "aws_partition"), _string(document, "aws_region")
    if not _SLUG.fullmatch(target) or stage not in _STAGES:
        raise ContractError("target_slug or stage is invalid")
    if not _ACCOUNT.fullmatch(account) or not _REGION.fullmatch(region):
        raise ContractError("AWS account id or region is invalid")
    expected_partition = "aws-us-gov" if region.startswith("us-gov-") else "aws"
    if partition != expected_partition:
        raise ContractError("AWS partition does not match the selected region")
    if partition == "aws-us-gov":
        raise ContractError(
            "DataChord application deployment does not support GovCloud because "
            "ALB Cognito authentication is unavailable there"
        )
    if not _COMMIT.fullmatch(_string(document, "application_commit")):
        raise ContractError("application_commit must be a full 40-character Git SHA")
    arn_prefix = f"arn:{partition}:iam::{account}:"
    if not _string(document, "application_role_boundary_arn").startswith(
        f"{arn_prefix}policy/"
    ):
        raise ContractError(
            "application_role_boundary_arn must be an IAM policy in the selected "
            "account and partition"
        )
    if not _string(document, "deployer_role_arn").startswith(
        f"{arn_prefix}role/"
    ):
        raise ContractError(
            "deployer_role_arn must be an IAM role in the selected account and partition"
        )
    _validate_repository_url(_string(document, "application_repository_url"))
    expected_state_key = (
        "data-chord/prod/tofu.tfstate"
        if target == "bdf" and stage == "prod"
        else f"datachord/{target}/{stage}/tofu.tfstate"
    )
    if _string(document, "state_key") != expected_state_key:
        raise ContractError(f"state_key must be {expected_state_key!r}")


def _validate_repository_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise ContractError("application_repository_url must not contain credentials")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.path.endswith(".git")
        or parsed.query
        or parsed.fragment
    ):
        raise ContractError(
            "application_repository_url must be a credential-free HTTPS Git URL"
        )


def _validate_selection(document: dict[str, object], selected_target: str, selected_stage: str) -> None:
    target, stage = _string(document, "target_slug"), _string(document, "stage")
    if target != selected_target:
        raise ContractError(f"deployment contract selects target {target!r}, not {selected_target!r}")
    if stage != selected_stage:
        raise ContractError(f"deployment contract selects stage {stage!r}, not {selected_stage!r}")


def _string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{key} must be a non-empty string")
    return value


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
