#!/usr/bin/env python3
"""Validate one checked-in DataChord deployment environment."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_FIELDS = {
    "account_id",
    "application_repository_url",
    "application_role_boundary_arn",
    "application_role_path",
    "deployer_role_arn",
    "domain_name",
    "github_app_secret_name",
    "hosted_zone_name",
    "region",
    "state_bucket_name",
}
_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")
_ACCOUNT = re.compile(r"^[0-9]{12}$")
_REGION = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_FOUNDATION_ROLE_NAME = re.compile(r"^[a-z][a-z0-9-]*-deployer$")
_STAGES = {"dev", "qa", "staging", "prod"}


class EnvironmentError(ValueError):
    """The deployment environment is missing, unsafe, or malformed."""


@dataclass(frozen=True)
class Environment:
    account_id: str
    region: str
    state_bucket_name: str
    deployer_role_arn: str
    application_role_boundary_arn: str
    application_role_path: str
    domain_name: str
    hosted_zone_name: str
    application_repository_url: str
    github_app_secret_name: str
    target: str
    stage: str

    @property
    def partition(self) -> str:
        return "aws-us-gov" if self.region.startswith("us-gov-") else "aws"

    @property
    def state_key(self) -> str:
        return f"datachord/{self.target}/{self.stage}/tofu.tfstate"

    @property
    def domain_label(self) -> str:
        return self.domain_name[: -len(f".{self.hosted_zone_name}")]

    @property
    def deployer_role_name(self) -> str:
        return self.deployer_role_arn.rsplit("/", maxsplit=1)[-1]

    @property
    def foundation_name_prefix(self) -> str:
        return self.deployer_role_name.removesuffix("-deployer")

    @property
    def deployer_boundary_arn(self) -> str:
        return (
            f"arn:{self.partition}:iam::{self.account_id}:policy/"
            f"{self.foundation_name_prefix}-deployer-boundary"
        )

    def tofu_variables(self) -> dict[str, object]:
        return {
            "application_repository_url": self.application_repository_url,
            "application_role_boundary_arn": self.application_role_boundary_arn,
            "application_role_path": self.application_role_path,
            "aws_region": self.region,
            "deployment_target": self.target,
            "domain_label": self.domain_label,
            "environment": self.stage,
            "expected_account_id": self.account_id,
            "github_app_secret_name": self.github_app_secret_name,
            "hosted_zone_name": self.hosted_zone_name,
        }


def load_environment(path: Path, target: str, stage: str) -> Environment:
    _validate_selection(target, stage)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EnvironmentError(f"environment does not exist: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvironmentError(f"environment is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise EnvironmentError("environment must contain one JSON object")
    document = {str(key): value for key, value in raw.items()}
    _require_exact_fields(document, _FIELDS, "environment")
    environment = Environment(
        account_id=_string(document, "account_id"),
        region=_string(document, "region"),
        state_bucket_name=_string(document, "state_bucket_name"),
        deployer_role_arn=_string(document, "deployer_role_arn"),
        application_role_boundary_arn=_string(document, "application_role_boundary_arn"),
        application_role_path=_string(document, "application_role_path"),
        domain_name=_string(document, "domain_name"),
        hosted_zone_name=_string(document, "hosted_zone_name").removesuffix("."),
        application_repository_url=_string(document, "application_repository_url"),
        github_app_secret_name=_string(document, "github_app_secret_name"),
        target=target,
        stage=stage,
    )
    _validate(environment)
    return environment


def canonical_digest(environment: Environment) -> str:
    encoded = json.dumps(
        {
            "account_id": environment.account_id,
            "application_repository_url": environment.application_repository_url,
            "application_role_boundary_arn": environment.application_role_boundary_arn,
            "application_role_path": environment.application_role_path,
            "deployer_role_arn": environment.deployer_role_arn,
            "domain_name": environment.domain_name,
            "github_app_secret_name": environment.github_app_secret_name,
            "hosted_zone_name": environment.hosted_zone_name,
            "region": environment.region,
            "stage": environment.stage,
            "state_bucket_name": environment.state_bucket_name,
            "target": environment.target,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_selection(target: str, stage: str) -> None:
    if not _SLUG.fullmatch(target):
        raise EnvironmentError("target must be a lowercase slug")
    if stage not in _STAGES:
        raise EnvironmentError("stage must be dev, qa, staging, or prod")


def _validate(environment: Environment) -> None:
    if not _ACCOUNT.fullmatch(environment.account_id):
        raise EnvironmentError("account_id must be a 12-digit AWS account id")
    if not _REGION.fullmatch(environment.region):
        raise EnvironmentError("region is invalid")
    if environment.partition == "aws-us-gov":
        raise EnvironmentError(
            "this branch does not support GovCloud because its ALB Cognito authentication is unavailable"
        )
    if not _BUCKET.fullmatch(environment.state_bucket_name):
        raise EnvironmentError("state_bucket_name is invalid")
    _validate_foundation_names(environment)
    suffix = f".{environment.hosted_zone_name}"
    if not environment.hosted_zone_name or not environment.domain_name.endswith(suffix):
        raise EnvironmentError("domain_name must be inside hosted_zone_name")
    if "." in environment.domain_label or not _SLUG.fullmatch(environment.domain_label):
        raise EnvironmentError("domain_name must add one lowercase DNS label")
    _validate_repository_url(environment.application_repository_url)


def _validate_foundation_names(environment: Environment) -> None:
    role_prefix = (
        f"arn:{environment.partition}:iam::{environment.account_id}:"
        "role/foundation/"
    )
    if not environment.deployer_role_arn.startswith(role_prefix):
        raise EnvironmentError("deployer role must use the /foundation/ path")
    if not _FOUNDATION_ROLE_NAME.fullmatch(environment.deployer_role_name):
        raise EnvironmentError("deployer role name must end with -deployer")
    expected_boundary = (
        f"arn:{environment.partition}:iam::{environment.account_id}:"
        f"policy/{environment.foundation_name_prefix}-application-role-boundary"
    )
    if environment.application_role_boundary_arn != expected_boundary:
        raise EnvironmentError(
            f"application_role_boundary_arn must be {expected_boundary}"
        )
    if environment.application_role_path != "/application/":
        raise EnvironmentError("application_role_path must be /application/")


def _validate_repository_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise EnvironmentError("application_repository_url must not contain credentials")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.path.endswith(".git")
        or parsed.query
        or parsed.fragment
    ):
        raise EnvironmentError(
            "application_repository_url must be a credential-free HTTPS Git URL ending in .git"
        )


def _require_exact_fields(
    document: dict[str, object], expected: set[str], label: str
) -> None:
    extra = sorted(set(document) - expected)
    missing = sorted(expected - set(document))
    if not extra and not missing:
        return
    messages: list[str] = []
    if extra:
        messages.append(f"unsupported {label} fields: {', '.join(extra)}")
    if missing:
        messages.append(f"missing {label} fields: {', '.join(missing)}")
    raise EnvironmentError("; ".join(messages))


def _string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise EnvironmentError(f"{key} must be a non-empty string")
    return value


def _field(environment: Environment, key: str) -> str:
    values = {
        "account_id": environment.account_id,
        "application_repository_url": environment.application_repository_url,
        "application_role_boundary_arn": environment.application_role_boundary_arn,
        "application_role_path": environment.application_role_path,
        "deployer_role_arn": environment.deployer_role_arn,
        "deployer_role_name": environment.deployer_role_name,
        "deployer_boundary_arn": environment.deployer_boundary_arn,
        "domain_label": environment.domain_label,
        "domain_name": environment.domain_name,
        "github_app_secret_name": environment.github_app_secret_name,
        "hosted_zone_name": environment.hosted_zone_name,
        "partition": environment.partition,
        "region": environment.region,
        "stage": environment.stage,
        "state_bucket_name": environment.state_bucket_name,
        "state_key": environment.state_key,
        "target": environment.target,
    }
    try:
        return values[key]
    except KeyError as exc:
        raise EnvironmentError(f"unknown environment field: {key}") from exc


def _main(arguments: list[str]) -> int:
    try:
        if len(arguments) < 4:
            raise EnvironmentError(
                "usage: environment.py validate|get|digest|tofu-vars FILE TARGET STAGE [VALUE]"
            )
        action, path, target, stage = arguments[:4]
        environment = load_environment(Path(path), target, stage)
        if action == "validate" and len(arguments) == 4:
            return 0
        if action == "get" and len(arguments) == 5:
            print(_field(environment, arguments[4]))
            return 0
        if action == "digest" and len(arguments) == 4:
            print(canonical_digest(environment))
            return 0
        if action == "tofu-vars" and len(arguments) == 4:
            print(json.dumps(environment.tofu_variables(), sort_keys=True))
            return 0
        raise EnvironmentError(
            "usage: environment.py validate|get|digest|tofu-vars FILE TARGET STAGE [VALUE]"
        )
    except EnvironmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
