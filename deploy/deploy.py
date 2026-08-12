"""Plan, deploy, or inspect one Data Chord target and stage."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess  # nosec B404
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

_PROJECT_NAME = "data-chord"
_CONTRACT_PARAMETER = "/datachord/foundation/deployment-contract"
_DEPLOYMENT_ROLE_PATH = "/foundation/datachord-deployer"
_CONTRACT_FIELDS = frozenset(
    {
        "schema_version",
        "target_slug",
        "aws_partition",
        "aws_account_id",
        "aws_region",
        "state_bucket_name",
        "deployment_role_arn",
        "application_role_path",
        "application_role_boundary_arn",
        "application_dns_zone_name",
        "data_model_store_url",
    }
)
_TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_+=,.@-]+$")
_EXISTING_BDF_DEPLOYMENTS = frozenset({("bdf", "staging"), ("bdf", "prod")})
_BUILD_TARGETS = (
    "aws_ecr_repository.app",
    "aws_cloudwatch_log_group.codebuild",
    "aws_iam_role.application_build",
    "aws_iam_role_policy.application_build",
    "aws_codebuild_project.app_image",
)
_CANONICAL_REPOSITORY = "https://github.com/netrias/data_chord.git"
_DEPLOYMENT_CONTROLLED_VARIABLES = frozenset(
    {
        "application_role_boundary_arn",
        "application_role_path",
        "auth_bypass_cidrs",
        "aws_partition",
        "aws_region",
        "data_model_store_url",
        "environment",
        "expected_account_id",
        "image_tag",
        "netrias_api_key_secret_name",
        "target_slug",
    }
)
_LEGACY_HANDOFF_ADDRESSES = frozenset(
    {
        "aws_iam_role.task_execution",
        "aws_iam_role_policy_attachment.task_execution",
        "aws_iam_role_policy.task_execution_secrets",
        "aws_iam_role.task",
        "aws_iam_role_policy.task_workflow_storage",
        "aws_iam_role.codebuild",
        "aws_iam_role_policy.codebuild",
        "aws_ecs_task_definition.app",
        "aws_security_group.secrets_endpoint[0]",
        "aws_vpc_endpoint_security_group_association.secretsmanager_tasks[0]",
    }
)


class Action(StrEnum):
    PLAN = "plan"
    DEPLOY = "deploy"
    STATUS = "status"


class Stage(StrEnum):
    DEV = "dev"
    QA = "qa"
    STAGING = "staging"
    PROD = "prod"


@dataclass(frozen=True)
class TargetSlug:
    value: str


@dataclass(frozen=True)
class OperatorRequest:
    action: Action
    target: TargetSlug
    stage: Stage
    profile: str


@dataclass(frozen=True)
class AwsIdentity:
    partition: str
    account_id: str
    arn: str


@dataclass(frozen=True)
class AwsSession:
    identity: AwsIdentity
    region: str
    deployment_role_arn: str
    environment: Mapping[str, str]


@dataclass(frozen=True)
class FoundationContract:
    target_slug: TargetSlug
    aws_partition: str
    aws_account_id: str
    aws_region: str
    state_bucket_name: str
    deployment_role_arn: str
    application_role_path: str
    application_role_boundary_arn: str
    application_dns_zone_name: str | None
    data_model_store_url: str


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one public operator command."""
    request = _parse_request(arguments)
    repo_root = Path(__file__).resolve().parent.parent
    infra_dir = repo_root / "infra"
    current_step = "preflight"
    _info(f"Running {request.action} for {request.target.value}/{request.stage.value}")

    try:
        session, contract = _preflight(request)

        if request.action is Action.STATUS:
            current_step = "service status"
            _show_status(request, session)
            return 0

        current_step = "stage configuration check"
        _require_stage_configuration(infra_dir, request, contract)

        current_step = "state migration check"
        _require_migrated_state(request, contract, session)

        current_step = "source validation"
        commit = _deployable_commit(repo_root)

        current_step = "secret validation"
        environment = _deployment_environment(request, contract, session, repo_root, commit)
        environment = _load_runtime_secrets(request, session, environment)

        current_step = "backend initialization"
        _initialize_backend(infra_dir, request, contract, environment)

        plans_root = repo_root / "build" / "plans"
        plans_root.mkdir(parents=True, exist_ok=True)
        plans_dir = Path(
            tempfile.mkdtemp(
                prefix=f"{request.target.value}-{request.stage.value}-",
                dir=plans_root,
            )
        )
        final_plan = plans_dir / "final.tfplan"

        if request.action is Action.PLAN:
            current_step = "saved OpenTofu plan"
            _create_saved_plan(infra_dir, request, final_plan, environment, read_only=True)
            current_step = "saved plan display"
            _show_saved_plan(infra_dir, final_plan, environment)
            _info(f"Saved plan: {final_plan}")
            _info(
                f"Plan complete for {request.target.value}/{request.stage.value}; "
                "no infrastructure was applied"
            )
            return 0

        current_step = "application role handoff check"
        _require_application_handoff_complete(infra_dir, request, environment)

        prerequisite_plan = plans_dir / "build-prerequisites.tfplan"
        current_step = "build prerequisite plan"
        _create_saved_plan(
            infra_dir,
            request,
            prerequisite_plan,
            environment,
            targets=_BUILD_TARGETS,
        )
        current_step = "build prerequisite plan display"
        prerequisite_digest = _show_saved_plan(
            infra_dir, prerequisite_plan, environment
        )
        current_step = "build prerequisite contract check"
        _require_unchanged_contract(request.target, session, contract)
        current_step = "build prerequisite apply"
        _apply_saved_plan(
            infra_dir,
            prerequisite_plan,
            prerequisite_digest,
            environment,
        )

        current_step = "build session refresh"
        session = _refreshed_session(request, contract)
        current_step = "container build"
        _build_image(request, commit, session, contract)

        current_step = "final session refresh"
        session = _refreshed_session(request, contract)
        environment = _deployment_environment(
            request, contract, session, repo_root, commit
        )
        environment = _load_runtime_secrets(request, session, environment)

        current_step = "final OpenTofu plan"
        _create_saved_plan(infra_dir, request, final_plan, environment)
        current_step = "final plan display"
        final_digest = _show_saved_plan(infra_dir, final_plan, environment)
        current_step = "final plan contract check"
        _require_unchanged_contract(request.target, session, contract)
        current_step = "final plan apply"
        _apply_saved_plan(infra_dir, final_plan, final_digest, environment)

        current_step = "service verification"
        _verify_service(request, session)

        current_step = "deployment output"
        app_url = _tofu_output(infra_dir, "app_url", environment)
    except KeyboardInterrupt:
        _error(f"{current_step} interrupted")
        return 130
    except (RuntimeError, ValueError) as error:
        _error(f"{current_step} failed: {error}")
        return 1

    _info(f"Deploy complete for {request.target.value}/{request.stage.value}: {app_url}")
    return 0


def _parse_request(arguments: Sequence[str] | None) -> OperatorRequest:
    parser = argparse.ArgumentParser(description="Plan, deploy, or inspect Data Chord")
    parser.add_argument("action", type=Action, choices=tuple(Action))
    parser.add_argument("target", type=_target_slug)
    parser.add_argument("stage", type=Stage, choices=tuple(Stage))
    parser.add_argument("profile", type=_profile_name)
    parsed = parser.parse_args(arguments)
    return OperatorRequest(parsed.action, parsed.target, parsed.stage, parsed.profile)


def _target_slug(value: str) -> TargetSlug:
    if not _TARGET_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("target must be a lowercase slug")
    return TargetSlug(value)


def _profile_name(value: str) -> str:
    if not _PROFILE_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("profile contains unsupported characters")
    return value


def _preflight(request: OperatorRequest) -> tuple[AwsSession, FoundationContract]:
    region_result = _run(
        ["aws", "configure", "get", "region", "--profile", request.profile],
        check=False,
    )
    region = region_result.stdout.strip()
    if region_result.returncode != 0 or not region:
        raise ValueError(f"AWS profile {request.profile!r} has no configured region")

    identity = _parse_identity(
        _run(
            [
                "aws",
                "sts",
                "get-caller-identity",
                "--profile",
                request.profile,
                "--region",
                region,
                "--output",
                "json",
            ]
        ).stdout
    )
    deployment_role_arn = (
        f"arn:{identity.partition}:iam::{identity.account_id}:role{_DEPLOYMENT_ROLE_PATH}"
    )
    environment = _profile_environment(request.profile, region)
    if not _is_deployment_role(identity, deployment_role_arn):
        environment = _assume_deployment_role(
            request.profile, region, identity, deployment_role_arn
        )

    session = AwsSession(identity, region, deployment_role_arn, environment)
    contract = _load_contract(request.target, session)
    return session, contract


def _parse_identity(payload: str) -> AwsIdentity:
    document = _json_object(payload, "caller identity")
    account_id = _required_string(document, "Account", "caller identity")
    arn = _required_string(document, "Arn", "caller identity")
    parts = arn.split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[4] != account_id:
        raise ValueError("caller identity returned an invalid ARN")
    if not re.fullmatch(r"[0-9]{12}", account_id):
        raise ValueError("caller identity returned an invalid account ID")
    return AwsIdentity(parts[1], account_id, arn)


def _is_deployment_role(identity: AwsIdentity, deployment_role_arn: str) -> bool:
    assumed_role_prefix = (
        f"arn:{identity.partition}:sts::{identity.account_id}:assumed-role/datachord-deployer/"
    )
    return identity.arn == deployment_role_arn or identity.arn.startswith(assumed_role_prefix)


def _profile_environment(profile: str, region: str) -> dict[str, str]:
    environment = _clean_environment()
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_DEFAULT_PROFILE",
    ):
        environment.pop(name, None)
    environment.update(
        {"AWS_PROFILE": profile, "AWS_REGION": region, "AWS_DEFAULT_REGION": region}
    )
    return environment


def _assume_deployment_role(
    profile: str,
    region: str,
    identity: AwsIdentity,
    deployment_role_arn: str,
) -> dict[str, str]:
    result = _run(
        [
            "aws",
            "sts",
            "assume-role",
            "--role-arn",
            deployment_role_arn,
            "--role-session-name",
            "data-chord-deploy",
            "--profile",
            profile,
            "--region",
            region,
            "--output",
            "json",
        ]
    )
    document = _json_object(result.stdout, "assume-role response")
    assumed_role = _object_field(document, "AssumedRoleUser", "assume-role response")
    assumed_arn = _required_string(assumed_role, "Arn", "assume-role response")
    assumed_identity = AwsIdentity(identity.partition, identity.account_id, assumed_arn)
    if not _is_deployment_role(assumed_identity, deployment_role_arn):
        raise ValueError("assume-role returned the wrong role identity")

    credentials = _object_field(document, "Credentials", "assume-role response")
    environment = _clean_environment()
    for name in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "AWS_ACCESS_KEY_ID": _required_string(
                credentials, "AccessKeyId", "assume-role response"
            ),
            "AWS_SECRET_ACCESS_KEY": _required_string(
                credentials, "SecretAccessKey", "assume-role response"
            ),
            "AWS_SESSION_TOKEN": _required_string(
                credentials, "SessionToken", "assume-role response"
            ),
            "AWS_REGION": region,
            "AWS_DEFAULT_REGION": region,
        }
    )
    return environment


def _clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in tuple(environment):
        if (
            name.startswith("TF_VAR_")
            or name.startswith("TF_CLI_ARGS")
            or name in {"TF_DATA_DIR", "TF_WORKSPACE"}
        ):
            environment.pop(name)
    return environment


def _load_contract(target: TargetSlug, session: AwsSession) -> FoundationContract:
    result = _run(
        [
            "aws",
            "ssm",
            "get-parameter",
            "--name",
            _CONTRACT_PARAMETER,
            "--query",
            "Parameter.Value",
            "--output",
            "text",
            "--region",
            session.region,
        ],
        environment=session.environment,
    )
    document = _json_object(result.stdout, "foundation deployment contract")
    fields = frozenset(document)
    if fields != _CONTRACT_FIELDS:
        missing = sorted(_CONTRACT_FIELDS - fields)
        extra = sorted(fields - _CONTRACT_FIELDS)
        raise ValueError(f"foundation contract fields differ (missing={missing}, extra={extra})")
    if document["schema_version"] != 1 or isinstance(document["schema_version"], bool):
        raise ValueError("foundation contract schema_version must be 1")

    contract = FoundationContract(
        target_slug=TargetSlug(
            _required_string(document, "target_slug", "foundation deployment contract")
        ),
        aws_partition=_required_string(
            document, "aws_partition", "foundation deployment contract"
        ),
        aws_account_id=_required_string(
            document, "aws_account_id", "foundation deployment contract"
        ),
        aws_region=_required_string(document, "aws_region", "foundation deployment contract"),
        state_bucket_name=_required_string(
            document, "state_bucket_name", "foundation deployment contract"
        ),
        deployment_role_arn=_required_string(
            document, "deployment_role_arn", "foundation deployment contract"
        ),
        application_role_path=_required_string(
            document, "application_role_path", "foundation deployment contract"
        ),
        application_role_boundary_arn=_required_string(
            document, "application_role_boundary_arn", "foundation deployment contract"
        ),
        application_dns_zone_name=_optional_string(
            document, "application_dns_zone_name", "foundation deployment contract"
        ),
        data_model_store_url=_required_string(
            document, "data_model_store_url", "foundation deployment contract"
        ),
    )
    _validate_contract(contract, target, session)
    return contract


def _validate_contract(
    contract: FoundationContract,
    target: TargetSlug,
    session: AwsSession,
) -> None:
    expected_values = (
        ("target", target.value, contract.target_slug.value),
        ("partition", session.identity.partition, contract.aws_partition),
        ("account", session.identity.account_id, contract.aws_account_id),
        ("region", session.region, contract.aws_region),
        ("deployment role", session.deployment_role_arn, contract.deployment_role_arn),
    )
    for name, selected, published in expected_values:
        if selected != published:
            raise ValueError(
                f"foundation contract {name} mismatch: "
                f"selected {selected!r}, published {published!r}"
            )

    if not _TARGET_PATTERN.fullmatch(contract.target_slug.value):
        raise ValueError("foundation contract target_slug is invalid")
    if contract.aws_partition not in {"aws", "aws-us-gov"}:
        raise ValueError("foundation contract aws_partition is unsupported")
    if not re.fullmatch(r"[0-9]{12}", contract.aws_account_id):
        raise ValueError("foundation contract aws_account_id is invalid")
    if not (
        contract.application_role_path.startswith("/")
        and contract.application_role_path.endswith("/")
    ):
        raise ValueError("foundation contract application_role_path must start and end with '/'")
    boundary_prefix = (
        f"arn:{contract.aws_partition}:iam::{contract.aws_account_id}:policy/"
    )
    if not contract.application_role_boundary_arn.startswith(boundary_prefix):
        raise ValueError("foundation contract application_role_boundary_arn is invalid")
    try:
        data_model_url = urlparse(contract.data_model_store_url)
        data_model_port = data_model_url.port
    except ValueError as error:
        raise ValueError(
            "foundation contract data_model_store_url has an invalid port"
        ) from error
    if (
        contract.data_model_store_url != contract.data_model_store_url.strip()
        or data_model_url.scheme != "https"
        or data_model_url.hostname is None
        or data_model_url.username is not None
        or data_model_url.password is not None
        or data_model_url.query
        or data_model_url.fragment
        or data_model_port is not None
        and not 1 <= data_model_port <= 65535
    ):
        raise ValueError(
            "foundation contract data_model_store_url must be an HTTPS URL "
            "without credentials, query, or fragment and with a valid port"
        )


def _require_unchanged_contract(
    target: TargetSlug,
    session: AwsSession,
    planned_contract: FoundationContract,
) -> None:
    current_contract = _load_contract(target, session)
    if _contract_digest(current_contract) != _contract_digest(planned_contract):
        raise RuntimeError(
            "foundation deployment contract changed after plan creation; "
            "create a fresh plan"
        )


def _refreshed_session(
    request: OperatorRequest,
    planned_contract: FoundationContract,
) -> AwsSession:
    session, current_contract = _preflight(request)
    if _contract_digest(current_contract) != _contract_digest(planned_contract):
        raise RuntimeError(
            "foundation deployment contract changed during deployment; "
            "create a fresh plan"
        )
    return session


def _contract_digest(contract: FoundationContract) -> str:
    document = json.dumps(
        asdict(contract),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(document.encode()).hexdigest()


def _require_stage_configuration(
    infra_dir: Path,
    request: OperatorRequest,
    contract: FoundationContract,
) -> None:
    configuration = (
        infra_dir / "env" / request.target.value / f"{request.stage.value}.tfvars"
    )
    if not configuration.is_file():
        raise RuntimeError(
            f"stage configuration does not exist: "
            f"infra/env/{request.target.value}/{request.stage.value}.tfvars"
        )
    protected_variables = set(_DEPLOYMENT_CONTROLLED_VARIABLES)
    if contract.application_dns_zone_name is not None:
        protected_variables.add("hosted_zone_name")
    assignment = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)
    configured_variables = set(assignment.findall(configuration.read_text()))
    forbidden_variables = sorted(protected_variables & configured_variables)
    if forbidden_variables:
        raise RuntimeError(
            "stage configuration sets deployment-controlled variables: "
            f"{', '.join(forbidden_variables)}"
        )


def _require_migrated_state(
    request: OperatorRequest,
    contract: FoundationContract,
    session: AwsSession,
) -> None:
    if (request.target.value, request.stage.value) not in _EXISTING_BDF_DEPLOYMENTS:
        return
    key = _state_key(request)
    result = _run(
        [
            "aws",
            "s3api",
            "head-object",
            "--bucket",
            contract.state_bucket_name,
            "--key",
            key,
            "--region",
            contract.aws_region,
        ],
        environment=session.environment,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot confirm migrated state at s3://{contract.state_bucket_name}/{key}; "
            "complete the state migration before plan or deploy"
        )


def _deployable_commit(repo_root: Path) -> str:
    dirty = _run(["git", "status", "--porcelain"], cwd=repo_root).stdout.strip()
    if dirty:
        raise RuntimeError("working tree has uncommitted changes; commit them before plan or deploy")
    commit = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("git returned an invalid source commit")
    branch = _run(["git", "branch", "--show-current"], cwd=repo_root).stdout.strip()
    if not branch:
        raise RuntimeError("cannot plan or deploy from a detached HEAD")
    remote_commit = _run(
        ["git", "ls-remote", _CANONICAL_REPOSITORY, f"refs/heads/{branch}"],
        cwd=repo_root,
    ).stdout.split()
    if not remote_commit or remote_commit[0] != commit:
        raise RuntimeError(
            f"canonical branch {branch!r} does not match local HEAD; "
            "push it to netrias/data_chord before plan or deploy"
        )
    _info(f"Deploy source: {branch} @ {commit[:12]}")
    return commit


def _deployment_environment(
    request: OperatorRequest,
    contract: FoundationContract,
    session: AwsSession,
    repo_root: Path,
    commit: str,
) -> dict[str, str]:
    environment = dict(session.environment)
    environment.update(
        {
            "TF_DATA_DIR": str(
                repo_root / "build" / "tofu" / request.target.value / request.stage.value
            ),
            "TF_VAR_target_slug": request.target.value,
            "TF_VAR_environment": request.stage.value,
            "TF_VAR_aws_partition": contract.aws_partition,
            "TF_VAR_expected_account_id": contract.aws_account_id,
            "TF_VAR_aws_region": contract.aws_region,
            "TF_VAR_application_role_path": contract.application_role_path,
            "TF_VAR_application_role_boundary_arn": (
                contract.application_role_boundary_arn
            ),
            "TF_VAR_data_model_store_url": contract.data_model_store_url,
            "TF_VAR_netrias_api_key_secret_name": (
                f"data-chord/{request.stage.value}/netrias-api-key"
            ),
            "TF_VAR_image_tag": commit[:12],
        }
    )
    if contract.application_dns_zone_name is not None:
        environment["TF_VAR_hosted_zone_name"] = contract.application_dns_zone_name
    return environment


def _load_runtime_secrets(
    request: OperatorRequest,
    session: AwsSession,
    environment: Mapping[str, str],
) -> dict[str, str]:
    deployment_environment = dict(environment)
    api_secret = f"data-chord/{request.stage.value}/netrias-api-key"
    _run(
        [
            "aws",
            "secretsmanager",
            "describe-secret",
            "--secret-id",
            api_secret,
            "--region",
            session.region,
        ],
        environment=deployment_environment,
    )

    bypass_secret = f"data-chord/{request.stage.value}/auth-bypass-cidrs"
    result = _run(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            bypass_secret,
            "--query",
            "SecretString",
            "--output",
            "text",
            "--region",
            session.region,
        ],
        environment=deployment_environment,
        check=False,
    )
    if result.returncode != 0:
        if "ResourceNotFoundException" in result.stderr:
            _info(f"No auth bypass CIDR secret found: {bypass_secret}")
            return deployment_environment
        raise RuntimeError(
            f"could not read auth bypass CIDR secret {bypass_secret!r}: "
            f"{result.stderr.strip()}"
        )
    cidrs = _parse_cidrs(result.stdout, bypass_secret)
    deployment_environment["TF_VAR_auth_bypass_cidrs"] = json.dumps(cidrs)
    return deployment_environment


def _parse_cidrs(payload: str, secret_name: str) -> list[str]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"secret {secret_name!r} must be a JSON array of CIDRs") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"secret {secret_name!r} must be a JSON array of CIDRs")
    for item in value:
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError as error:
            raise ValueError(f"secret {secret_name!r} contains invalid CIDR {item!r}") from error
    return value


def _initialize_backend(
    infra_dir: Path,
    request: OperatorRequest,
    contract: FoundationContract,
    environment: Mapping[str, str],
) -> None:
    _run(
        [
            "tofu",
            "init",
            "-input=false",
            "-reconfigure",
            f"-backend-config=bucket={contract.state_bucket_name}",
            f"-backend-config=key={_state_key(request)}",
            f"-backend-config=region={contract.aws_region}",
            "-backend-config=encrypt=true",
            "-backend-config=use_lockfile=true",
        ],
        cwd=infra_dir,
        environment=environment,
        capture_output=False,
    )


def _state_key(request: OperatorRequest) -> str:
    return (
        f"datachord/{request.target.value}/{request.stage.value}/tofu.tfstate"
    )


def _require_application_handoff_complete(
    infra_dir: Path,
    request: OperatorRequest,
    environment: Mapping[str, str],
) -> None:
    if (request.target.value, request.stage.value) not in _EXISTING_BDF_DEPLOYMENTS:
        return
    state_addresses = set(
        _run(
            ["tofu", "state", "list"],
            cwd=infra_dir,
            environment=environment,
        ).stdout.splitlines()
    )
    legacy_addresses = sorted(_LEGACY_HANDOFF_ADDRESSES & state_addresses)
    if legacy_addresses:
        raise RuntimeError(
            "legacy BDF handoff resources remain in state; complete the configured-operator "
            f"saved-plan handoff first: {', '.join(legacy_addresses)}"
        )


def _create_saved_plan(
    infra_dir: Path,
    request: OperatorRequest,
    saved_plan: Path,
    environment: Mapping[str, str],
    *,
    read_only: bool = False,
    targets: Sequence[str] = (),
) -> None:
    command = [
        "tofu",
        "plan",
        "-input=false",
        f"-var-file=env/{request.target.value}/{request.stage.value}.tfvars",
    ]
    command.extend(_protected_variable_arguments(environment))
    command.append(f"-out={saved_plan}")
    if read_only:
        command.append("-lock=false")
    command.extend(f"-target={target}" for target in targets)
    _run(
        command,
        cwd=infra_dir,
        environment=environment,
        capture_output=False,
    )


def _show_saved_plan(
    infra_dir: Path,
    saved_plan: Path,
    environment: Mapping[str, str],
) -> str:
    _run(
        ["tofu", "show", str(saved_plan)],
        cwd=infra_dir,
        environment=environment,
        capture_output=False,
    )
    return _file_digest(saved_plan)


def _apply_saved_plan(
    infra_dir: Path,
    saved_plan: Path,
    shown_digest: str,
    environment: Mapping[str, str],
) -> None:
    if _file_digest(saved_plan) != shown_digest:
        raise RuntimeError(f"saved plan changed after display: {saved_plan}")
    _run(
        ["tofu", "apply", "-input=false", str(saved_plan)],
        cwd=infra_dir,
        environment=environment,
        capture_output=False,
    )


def _protected_variable_arguments(environment: Mapping[str, str]) -> list[str]:
    prefix = "TF_VAR_"
    return [
        f"-var={name.removeprefix(prefix)}={value}"
        for name, value in sorted(environment.items())
        if name.startswith(prefix)
    ]


def _file_digest(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"could not read saved plan {path}: {error}") from error
    return hashlib.sha256(content).hexdigest()


def _build_image(
    request: OperatorRequest,
    commit: str,
    session: AwsSession,
    planned_contract: FoundationContract,
) -> None:
    resource_name = f"{_PROJECT_NAME}-{request.stage.value}"
    image_tag = commit[:12]
    image = _run(
        [
            "aws",
            "ecr",
            "describe-images",
            "--repository-name",
            resource_name,
            "--image-ids",
            f"imageTag={image_tag}",
            "--region",
            session.region,
        ],
        environment=session.environment,
        check=False,
    )
    if image.returncode == 0:
        _info(f"Reusing immutable image {resource_name}:{image_tag}")
        return
    if "ImageNotFoundException" not in image.stderr:
        raise RuntimeError(
            f"could not check ECR image {resource_name}:{image_tag}: "
            f"{image.stderr.strip()}"
        )

    build_id = _run(
        [
            "aws",
            "codebuild",
            "start-build",
            "--project-name",
            f"{resource_name}-image",
            "--source-version",
            commit,
            "--query",
            "build.id",
            "--output",
            "text",
            "--region",
            session.region,
        ],
        environment=session.environment,
    ).stdout.strip()
    if not build_id:
        raise RuntimeError("CodeBuild did not return a build id")
    _info(f"Started CodeBuild build {build_id}")

    deadline = time.monotonic() + 65 * 60
    refresh_at = time.monotonic() + 45 * 60
    current_session = session
    while time.monotonic() < deadline:
        if time.monotonic() >= refresh_at:
            current_session = _refreshed_session(request, planned_contract)
            refresh_at = time.monotonic() + 45 * 60
        result = _run(
            [
                "aws",
                "codebuild",
                "batch-get-builds",
                "--ids",
                build_id,
                "--query",
                "builds[0].{status:buildStatus,phase:currentPhase}",
                "--output",
                "json",
                "--region",
                current_session.region,
            ],
            environment=current_session.environment,
        )
        status = _json_object(result.stdout, "CodeBuild status")
        state = _required_string(status, "status", "CodeBuild status")
        if state == "SUCCEEDED":
            return
        if state in {"FAILED", "FAULT", "STOPPED", "TIMED_OUT"}:
            phase = status.get("phase")
            raise RuntimeError(f"CodeBuild finished with status {state} in phase {phase}")
        time.sleep(10)
    raise RuntimeError("timed out waiting for CodeBuild after 65 minutes")


def _tofu_output(
    infra_dir: Path,
    name: str,
    environment: Mapping[str, str],
) -> str:
    value = _run(
        ["tofu", "output", "-raw", name],
        cwd=infra_dir,
        environment=environment,
    ).stdout.strip()
    if not value:
        raise RuntimeError(f"OpenTofu output {name!r} is not available")
    return value


def _show_status(request: OperatorRequest, session: AwsSession) -> None:
    status = _read_service_status(request, session)
    _info(json.dumps(status, sort_keys=True))


def _verify_service(request: OperatorRequest, session: AwsSession) -> None:
    service_name = f"{_PROJECT_NAME}-{request.stage.value}"
    _run(
        [
            "aws",
            "ecs",
            "wait",
            "services-stable",
            "--cluster",
            service_name,
            "--services",
            service_name,
            "--region",
            session.region,
        ],
        environment=session.environment,
        capture_output=False,
    )
    status = _read_service_status(request, session)
    if (
        status.get("status") != "ACTIVE"
        or status.get("rolloutState") != "COMPLETED"
        or status.get("runningCount") != status.get("desiredCount")
        or status.get("pendingCount") != 0
    ):
        raise RuntimeError(f"ECS service is not ready: {json.dumps(status, sort_keys=True)}")

    target_group_arn = _run(
        [
            "aws",
            "elbv2",
            "describe-target-groups",
            "--names",
            f"{service_name}-app",
            "--query",
            "TargetGroups[0].TargetGroupArn",
            "--output",
            "text",
            "--region",
            session.region,
        ],
        environment=session.environment,
    ).stdout.strip()
    if not target_group_arn or target_group_arn == "None":
        raise RuntimeError("application target group is not available")
    health = _run(
        [
            "aws",
            "elbv2",
            "describe-target-health",
            "--target-group-arn",
            target_group_arn,
            "--query",
            "TargetHealthDescriptions[].TargetHealth.State",
            "--output",
            "json",
            "--region",
            session.region,
        ],
        environment=session.environment,
    )
    target_states = _json_string_list(health.stdout, "application target health")
    if not target_states or any(state != "healthy" for state in target_states):
        raise RuntimeError(f"application targets are not healthy: {target_states}")
    _info(f"Verified {service_name}: completed ECS rollout with healthy targets")


def _read_service_status(
    request: OperatorRequest,
    session: AwsSession,
) -> dict[str, object]:
    service_name = f"{_PROJECT_NAME}-{request.stage.value}"
    result = _run(
        [
            "aws",
            "ecs",
            "describe-services",
            "--cluster",
            service_name,
            "--services",
            service_name,
            "--query",
            (
                "services[0].{status:status,desiredCount:desiredCount,"
                "runningCount:runningCount,pendingCount:pendingCount,"
                "rolloutState:deployments[?status=='PRIMARY']|[0].rolloutState}"
            ),
            "--output",
            "json",
            "--region",
            session.region,
        ],
        environment=session.environment,
    )
    return _json_object(result.stdout, "ECS service status")


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    # Required operator tools run as fixed argument vectors without a shell.
    try:
        return subprocess.run(  # noqa: S603  # nosec B603
            command,
            cwd=cwd,
            env=environment,
            check=check,
            capture_output=capture_output,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"required command is not installed: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"command failed: {' '.join(command)}{suffix}") from error


def _json_object(payload: str, source: str) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"{source} is not valid JSON") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{source} must be a JSON object")
    return value


def _json_string_list(payload: str, source: str) -> list[str]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"{source} is not valid JSON") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{source} must be a JSON array of strings")
    return value


def _object_field(
    document: Mapping[str, object], field: str, source: str
) -> dict[str, object]:
    value = document.get(field)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{source} field {field!r} must be an object")
    return value


def _required_string(document: Mapping[str, object], field: str, source: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source} field {field!r} must be a non-empty string")
    return value


def _optional_string(
    document: Mapping[str, object], field: str, source: str
) -> str | None:
    value = document.get(field)
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{source} field {field!r} must be null or a non-empty string")
    return value


def _info(message: str) -> None:
    print(f"[data-chord] {message}")


def _error(message: str) -> None:
    print(f"[data-chord] ERROR: {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
