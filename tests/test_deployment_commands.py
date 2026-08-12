"""Operator-level proof for the Data Chord deployment commands."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import deploy.deploy as deployment

_ACCOUNT_ID = "123456789012"
_REGION = "us-west-2"
_PROFILE = "customer-admin"
_TARGET = "netrias"


class MockCommandRunner:
    """Return realistic tool responses while recording externally visible effects."""

    def __init__(self, contract_overrides: Mapping[str, object] | None = None) -> None:
        contract: dict[str, object] = {
            "schema_version": 1,
            "target_slug": _TARGET,
            "aws_partition": "aws",
            "aws_account_id": _ACCOUNT_ID,
            "aws_region": _REGION,
            "state_bucket_name": "foundation-state-123",
            "deployment_role_arn": f"arn:aws:iam::{_ACCOUNT_ID}:role/foundation/datachord-deployer",
            "application_role_path": "/application/",
            "application_role_boundary_arn": (
                f"arn:aws:iam::{_ACCOUNT_ID}:policy/datachord-application-role-boundary"
            ),
            "application_dns_zone_name": "apps.example.com",
            "data_model_store_url": "https://model.example.com",
        }
        contract.update(contract_overrides or {})
        self.contract = contract
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        self.environments.append(dict(environment or {}))
        if command_tuple[:2] == ("tofu", "plan"):
            plan_path = next(
                Path(argument.removeprefix("-out="))
                for argument in command_tuple
                if argument.startswith("-out=")
            )
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_bytes(b"saved OpenTofu plan")
        if command_tuple[:3] == ("aws", "ecr", "describe-images"):
            return subprocess.CompletedProcess(
                command_tuple,
                1,
                stdout="",
                stderr="ImageNotFoundException",
            )
        output = self._output(command_tuple)
        return subprocess.CompletedProcess(command_tuple, 0, stdout=output, stderr="")

    def _output(self, command: tuple[str, ...]) -> str:  # noqa: C901
        if command[:4] == ("aws", "configure", "get", "region"):
            return f"{_REGION}\n"
        if command[:3] == ("aws", "sts", "get-caller-identity"):
            return json.dumps(
                {
                    "Account": _ACCOUNT_ID,
                    "Arn": f"arn:aws:iam::{_ACCOUNT_ID}:role/foundation/datachord-deployer",
                }
            )
        if command[:3] == ("aws", "ssm", "get-parameter"):
            return json.dumps(self.contract)
        if command[:3] == ("aws", "secretsmanager", "get-secret-value"):
            return "[]\n"
        if command[:3] == ("aws", "secretsmanager", "describe-secret"):
            return "{}\n"
        if command[:3] == ("git", "status", "--porcelain"):
            return ""
        if command[:3] == ("git", "rev-parse", "HEAD"):
            return "0123456789abcdef0123456789abcdef01234567\n"
        if command[:3] == ("git", "branch", "--show-current"):
            return "deployment-contract\n"
        if command[:2] == ("git", "ls-remote"):
            return "0123456789abcdef0123456789abcdef01234567\trefs/heads/deployment-contract\n"
        if command[:3] == ("tofu", "output", "-raw"):
            output_name = command[3]
            return {
                "app_url": "https://data-chord.apps.example.com\n",
            }[output_name]
        if command[:3] == ("aws", "codebuild", "start-build"):
            return "data-chord-staging-image:build-1\n"
        if command[:3] == ("aws", "codebuild", "batch-get-builds"):
            return json.dumps({"status": "SUCCEEDED", "phase": "COMPLETED"})
        if command[:3] == ("aws", "ecs", "describe-services"):
            return json.dumps(
                {
                    "status": "ACTIVE",
                    "desiredCount": 1,
                    "runningCount": 1,
                    "pendingCount": 0,
                    "rolloutState": "COMPLETED",
                }
            )
        if command[:3] == ("aws", "elbv2", "describe-target-groups"):
            return "arn:aws:elasticloadbalancing:us-west-2:123456789012:targetgroup/data-chord-staging-app/1\n"
        if command[:3] == ("aws", "elbv2", "describe-target-health"):
            return json.dumps(["healthy"])
        return ""


def _run(
    monkeypatch: pytest.MonkeyPatch,
    runner: MockCommandRunner,
    action: str,
    target: str = _TARGET,
) -> int:
    monkeypatch.setattr(deployment, "_run", runner)
    return deployment.main([action, target, "staging", _PROFILE])


def _has_command(runner: MockCommandRunner, *prefix: str) -> bool:
    return any(command[: len(prefix)] == prefix for command in runner.commands)


def test_plan_saves_and_shows_a_read_only_plan(monkeypatch, capsys) -> None:
    runner = MockCommandRunner()

    result = _run(monkeypatch, runner, "plan")

    assert result == 0
    assert "no infrastructure was applied" in capsys.readouterr().out
    assert _has_command(runner, "tofu", "init")
    plan = next(command for command in runner.commands if command[:2] == ("tofu", "plan"))
    assert "-lock=false" in plan
    assert any(argument.startswith("-out=") for argument in plan)
    assert _has_command(runner, "tofu", "show")
    assert not _has_command(runner, "tofu", "apply")
    assert not _has_command(runner, "aws", "codebuild", "start-build")


def test_deploy_builds_then_applies_the_displayed_final_plan(monkeypatch, capsys) -> None:
    runner = MockCommandRunner()

    result = _run(monkeypatch, runner, "deploy")

    assert result == 0
    output = capsys.readouterr().out
    assert "Verified data-chord-staging" in output
    assert _has_command(runner, "aws", "codebuild", "start-build")
    plans = [command for command in runner.commands if command[:2] == ("tofu", "plan")]
    shown = [command[-1] for command in runner.commands if command[:2] == ("tofu", "show")]
    applied = [command[-1] for command in runner.commands if command[:2] == ("tofu", "apply")]
    assert len(plans) == 2
    assert len(set(shown)) == 2
    assert len(shown) == 2
    assert applied == shown
    assert all("-auto-approve" not in command for command in runner.commands)


def test_status_only_queries_the_service(monkeypatch, capsys) -> None:
    runner = MockCommandRunner()

    result = _run(monkeypatch, runner, "status")

    assert result == 0
    assert '"status": "ACTIVE"' in capsys.readouterr().out
    assert _has_command(runner, "aws", "ecs", "describe-services")
    assert not any(command[0] == "tofu" for command in runner.commands)
    assert not _has_command(runner, "aws", "secretsmanager")
    assert not _has_command(runner, "aws", "codebuild")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target_slug", "other", "target mismatch"),
        ("aws_account_id", "999999999999", "account mismatch"),
        ("aws_region", "us-east-1", "region mismatch"),
        ("aws_partition", "aws-us-gov", "partition mismatch"),
    ],
)
def test_contract_mismatch_stops_before_state_or_secret_access(
    monkeypatch,
    capsys,
    field: str,
    value: str,
    message: str,
) -> None:
    runner = MockCommandRunner({field: value})

    result = _run(monkeypatch, runner, "plan")

    assert result == 1
    assert message in capsys.readouterr().err
    assert not any(command[0] == "tofu" for command in runner.commands)
    assert not _has_command(runner, "aws", "s3api")
    assert not _has_command(runner, "aws", "secretsmanager")
    assert not _has_command(runner, "aws", "codebuild")


def test_profile_region_and_partition_select_the_foundation_role(monkeypatch) -> None:
    account_id = "210987654321"
    region = "us-gov-west-1"
    expected_role = f"arn:aws-us-gov:iam::{account_id}:role/foundation/datachord-deployer"
    runner = MockCommandRunner(
        {
            "aws_partition": "aws-us-gov",
            "aws_account_id": account_id,
            "aws_region": region,
            "deployment_role_arn": expected_role,
            "application_role_boundary_arn": (
                f"arn:aws-us-gov:iam::{account_id}:policy/datachord-application-role-boundary"
            ),
        }
    )

    def govcloud_tools(
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        if command_tuple[:4] == ("aws", "configure", "get", "region"):
            runner.commands.append(command_tuple)
            runner.environments.append(dict(environment or {}))
            return subprocess.CompletedProcess(command_tuple, 0, stdout=f"{region}\n", stderr="")
        if command_tuple[:3] == ("aws", "sts", "get-caller-identity"):
            runner.commands.append(command_tuple)
            runner.environments.append(dict(environment or {}))
            identity = {"Account": account_id, "Arn": f"arn:aws-us-gov:iam::{account_id}:role/admin"}
            return subprocess.CompletedProcess(command_tuple, 0, stdout=json.dumps(identity), stderr="")
        if command_tuple[:3] == ("aws", "sts", "assume-role"):
            runner.commands.append(command_tuple)
            runner.environments.append(dict(environment or {}))
            response = {
                "AssumedRoleUser": {
                    "Arn": f"arn:aws-us-gov:sts::{account_id}:assumed-role/datachord-deployer/session"
                },
                "Credentials": {
                    "AccessKeyId": "test-access-key",
                    "SecretAccessKey": "test-secret-key",
                    "SessionToken": "test-session-token",
                },
            }
            return subprocess.CompletedProcess(command_tuple, 0, stdout=json.dumps(response), stderr="")
        return runner(
            command,
            cwd=cwd,
            environment=environment,
            check=check,
            capture_output=capture_output,
        )

    monkeypatch.setattr(deployment, "_run", govcloud_tools)

    assert deployment.main(["status", _TARGET, "staging", _PROFILE]) == 0
    assume_role = next(command for command in runner.commands if command[:3] == ("aws", "sts", "assume-role"))
    assert expected_role in assume_role
    contract_read = next(command for command in runner.commands if command[:3] == ("aws", "ssm", "get-parameter"))
    assert region in contract_read


def test_existing_bdf_stage_requires_migrated_service_state(monkeypatch, capsys) -> None:
    runner = MockCommandRunner(
        {"target_slug": "bdf", "application_dns_zone_name": None}
    )

    def missing_state(
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = runner(
            command,
            cwd=cwd,
            environment=environment,
            check=check,
            capture_output=capture_output,
        )
        if tuple(command)[:3] == ("aws", "s3api", "head-object"):
            return subprocess.CompletedProcess(tuple(command), 1, stdout="", stderr="not found")
        return result

    monkeypatch.setattr(deployment, "_run", missing_state)

    assert deployment.main(["plan", "bdf", "staging", _PROFILE]) == 1
    assert "cannot confirm migrated state" in capsys.readouterr().err
    assert not any(command[0] == "tofu" for command in runner.commands)


@pytest.mark.parametrize(
    "legacy_address",
    [
        "aws_iam_role.task",
        "aws_security_group.secrets_endpoint[0]",
        "aws_vpc_endpoint_security_group_association.secretsmanager_tasks[0]",
    ],
)
def test_deploy_stops_before_changes_when_the_bdf_handoff_is_incomplete(
    monkeypatch,
    capsys,
    legacy_address: str,
) -> None:
    runner = MockCommandRunner(
        {"target_slug": "bdf", "application_dns_zone_name": None}
    )

    def legacy_state(
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = runner(
            command,
            cwd=cwd,
            environment=environment,
            check=check,
            capture_output=capture_output,
        )
        if tuple(command)[:3] == ("tofu", "state", "list"):
            return subprocess.CompletedProcess(
                tuple(command), 0, stdout=f"{legacy_address}\n", stderr=""
            )
        return result

    monkeypatch.setattr(deployment, "_run", legacy_state)

    assert deployment.main(["deploy", "bdf", "staging", _PROFILE]) == 1
    error = capsys.readouterr().err
    assert "legacy BDF handoff resources remain in state" in error
    assert legacy_address in error
    assert not _has_command(runner, "tofu", "apply")
    assert not _has_command(runner, "aws", "codebuild", "start-build")


def test_new_customer_deploy_does_not_require_existing_state(
    monkeypatch,
) -> None:
    runner = MockCommandRunner()

    def no_existing_state(
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if tuple(command)[:3] == ("tofu", "state", "list"):
            raise AssertionError("a new customer must not inspect legacy BDF state")
        return runner(
            command,
            cwd=cwd,
            environment=environment,
            check=check,
            capture_output=capture_output,
        )

    monkeypatch.setattr(deployment, "_run", no_existing_state)

    assert deployment.main(["deploy", _TARGET, "staging", _PROFILE]) == 0
    assert not _has_command(runner, "tofu", "state", "list")


@pytest.mark.parametrize(
    "url",
    [
        "http://model.example.com",
        "https://user@model.example.com",
        "https://:secret@model.example.com",
        "https://model.example.com?target=other",
        "https://model.example.com#other",
        "https://model.example.com:not-a-port",
        "https:///models",
        " https://model.example.com",
    ],
)
def test_invalid_data_model_store_url_stops_before_state_access(
    monkeypatch,
    capsys,
    url: str,
) -> None:
    runner = MockCommandRunner({"data_model_store_url": url})

    assert _run(monkeypatch, runner, "plan") == 1
    assert "data_model_store_url" in capsys.readouterr().err
    assert not any(command[0] == "tofu" for command in runner.commands)
    assert not _has_command(runner, "aws", "s3api")
    assert not _has_command(runner, "aws", "secretsmanager")


def test_plan_ignores_ambient_opentofu_overrides(monkeypatch) -> None:
    runner = MockCommandRunner()
    monkeypatch.setenv("TF_VAR_expected_account_id", "999999999999")
    monkeypatch.setenv("TF_CLI_ARGS_plan", "-destroy")

    assert _run(monkeypatch, runner, "plan") == 0

    tofu_environments = [
        environment
        for command, environment in zip(
            runner.commands, runner.environments, strict=True
        )
        if command[0] == "tofu"
    ]
    assert tofu_environments
    assert all(
        environment["TF_VAR_expected_account_id"] == _ACCOUNT_ID
        and "TF_CLI_ARGS_plan" not in environment
        for environment in tofu_environments
    )


@pytest.mark.parametrize(("change_on_read", "expected_applies"), [(2, 0), (5, 1)])
def test_deploy_does_not_apply_a_plan_after_the_foundation_contract_changes(
    monkeypatch,
    capsys,
    change_on_read: int,
    expected_applies: int,
) -> None:
    runner = MockCommandRunner()
    ssm_reads = 0

    def changing_contract(
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal ssm_reads
        if tuple(command)[:3] == ("aws", "ssm", "get-parameter"):
            ssm_reads += 1
            if ssm_reads == change_on_read:
                runner.contract["data_model_store_url"] = (
                    "https://replacement-model.example.com"
                )
        return runner(
            command,
            cwd=cwd,
            environment=environment,
            check=check,
            capture_output=capture_output,
        )

    monkeypatch.setattr(deployment, "_run", changing_contract)

    assert deployment.main(["deploy", _TARGET, "staging", _PROFILE]) == 1
    assert "contract changed after plan creation" in capsys.readouterr().err
    applies = [
        command for command in runner.commands if command[:2] == ("tofu", "apply")
    ]
    assert len(applies) == expected_applies


def test_deploy_does_not_apply_a_plan_replaced_after_display(
    monkeypatch,
    capsys,
) -> None:
    runner = MockCommandRunner()
    ssm_reads = 0

    def replace_displayed_plan(
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal ssm_reads
        if tuple(command)[:3] == ("aws", "ssm", "get-parameter"):
            ssm_reads += 1
            if ssm_reads == 2:
                shown_plan = next(
                    Path(recorded[-1])
                    for recorded in reversed(runner.commands)
                    if recorded[:2] == ("tofu", "show")
                )
                shown_plan.write_bytes(b"replacement plan")
        return runner(
            command,
            cwd=cwd,
            environment=environment,
            check=check,
            capture_output=capture_output,
        )

    monkeypatch.setattr(deployment, "_run", replace_displayed_plan)

    assert deployment.main(["deploy", _TARGET, "staging", _PROFILE]) == 1
    assert "saved plan changed after display" in capsys.readouterr().err
    assert not _has_command(runner, "tofu", "apply")


def test_stage_cannot_override_deployment_controlled_values(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    runner = MockCommandRunner()
    module_path = tmp_path / "repo" / "deploy" / "deploy.py"
    module_path.parent.mkdir(parents=True)
    module_path.touch()
    stage_file = tmp_path / "repo" / "infra" / "env" / _TARGET / "staging.tfvars"
    stage_file.parent.mkdir(parents=True)
    stage_file.write_text('expected_account_id = "999999999999"\n')
    monkeypatch.setattr(deployment, "__file__", str(module_path))
    monkeypatch.setattr(deployment, "_run", runner)

    assert deployment.main(["plan", _TARGET, "staging", _PROFILE]) == 1
    assert "stage configuration sets deployment-controlled variables" in capsys.readouterr().err
    assert not any(command[0] == "tofu" for command in runner.commands)
    assert not _has_command(runner, "aws", "secretsmanager")


def test_source_must_match_a_live_branch_in_the_canonical_repository(
    monkeypatch,
    capsys,
) -> None:
    runner = MockCommandRunner()

    def stale_canonical_branch(
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if tuple(command)[:2] == ("git", "ls-remote"):
            command_tuple = tuple(command)
            runner.commands.append(command_tuple)
            runner.environments.append(dict(environment or {}))
            return subprocess.CompletedProcess(
                command_tuple,
                0,
                stdout="ffffffffffffffffffffffffffffffffffffffff\trefs/heads/deployment-contract\n",
                stderr="",
            )
        return runner(
            command,
            cwd=cwd,
            environment=environment,
            check=check,
            capture_output=capture_output,
        )

    monkeypatch.setattr(deployment, "_run", stale_canonical_branch)

    assert deployment.main(["plan", _TARGET, "staging", _PROFILE]) == 1
    assert "canonical branch" in capsys.readouterr().err
    assert not any(command[0] == "tofu" for command in runner.commands)
    assert not _has_command(runner, "aws", "secretsmanager")
