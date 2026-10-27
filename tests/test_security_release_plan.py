"""Exercise the production plan boundary with real serialized plan inputs."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).parents[1] / "infra/scripts/check-security-release-plan.py"
OLD_TAG = "a" * 40
NEW_TAG = "b" * 40
OLD_DIGEST = "sha256:" + "1" * 64
NEW_DIGEST = "sha256:" + "2" * 64
REPOSITORY = "084828580051.dkr.ecr.us-east-2.amazonaws.com/data-chord-prod"


def _containers(tag: str) -> str:
    return json.dumps([{"name": "app", "image": f"{REPOSITORY}:{tag}",
                        "environment": [{"name": "DATA_CHORD_ASSET_VERSION", "value": tag},
                                        {"name": "APPLICATION_SETTING", "value": "unchanged"}]}])


@pytest.fixture
def release_plan() -> dict:
    before = {"family": "data-chord-prod", "cpu": "1024", "container_definitions": _containers(OLD_TAG),
              "arn": "old-task", "revision": 10}
    after = {**before, "container_definitions": _containers(NEW_TAG), "arn": None, "revision": None}
    return {"configuration": {"root_module": {"resources": [{"address": "aws_ecs_service.app",
        "expressions": {"task_definition": {"references": ["aws_ecs_task_definition.app.arn"]}}}]}},
        "resource_changes": [
        {"address": "aws_ecs_task_definition.app", "change": {
            "actions": ["delete", "create"], "before": before, "after": after}},
        {"address": "aws_ecs_service.app", "change": {
            "actions": ["update"], "after_unknown": {"task_definition": True},
            "before": {"task_definition": "old-task",
                       "deployment_circuit_breaker": [{"enable": True, "rollback": True}]},
            "after": {"task_definition": None, "deployment_circuit_breaker": [{"enable": True, "rollback": False}]}}},
    ]}


def _check(
    tmp_path: Path, plan: dict, images: dict[str, str] | None,
    running_task_arn: str = "old-task", running_image_tag: str = OLD_TAG,
) -> subprocess.CompletedProcess[str]:
    plan_file = tmp_path / "plan.json"
    record = tmp_path / "qualification.json"
    plan_file.write_text(json.dumps(plan))
    command = [sys.executable, str(CHECK), str(plan_file)]
    if images is not None:
        record.write_text(json.dumps({"qualified_images": images}))
        command += ["--qualification", str(record)]
    service = tmp_path / "live-service.json"
    task = tmp_path / "live-task.json"
    service.write_text(json.dumps({"taskDefinition": running_task_arn, "deployments": [{
        "status": "PRIMARY", "rolloutState": "COMPLETED", "desiredCount": 1, "runningCount": 1,
        "pendingCount": 0, "taskDefinition": running_task_arn}]}))
    task.write_text(json.dumps({"taskDefinitionArn": running_task_arn,
                                "containerDefinitions": json.loads(_containers(running_image_tag))}))
    command += ["--running-service", str(service), "--running-task", str(task)]
    return subprocess.run(command,
                          capture_output=True, text=True, check=False)


def test_first_patched_baseline_disables_old_automatic_rollback(tmp_path: Path, release_plan: dict) -> None:
    # Given only the new image is qualified and the first rollout disables automatic rollback.
    images = {NEW_TAG: NEW_DIGEST}

    # When the real checker reads the saved plan and qualification record.
    result = _check(tmp_path, release_plan, images)

    # Then only the qualified new image is selected for registry verification.
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{NEW_TAG}\t{NEW_DIGEST}\n"


def test_automatic_rollback_cannot_restore_an_unqualified_image(tmp_path: Path, release_plan: dict) -> None:
    # Given automatic rollback would restore the unqualified older image.
    release_plan["resource_changes"][1]["change"]["after"]["deployment_circuit_breaker"][0]["rollback"] = True

    # When the actual release checker inspects that plan.
    result = _check(tmp_path, release_plan, {NEW_TAG: NEW_DIGEST})

    # Then it refuses the plan and emits no accepted image identities.
    assert result.returncode == 1
    assert "Unqualified release or automatic rollback image" in result.stderr
    assert result.stdout == ""


def test_later_rollout_requires_both_qualified_images(tmp_path: Path, release_plan: dict) -> None:
    # Given both the running baseline and new candidate have verified digests.
    release_plan["resource_changes"][1]["change"]["after"]["deployment_circuit_breaker"][0]["rollback"] = True

    # When the actual checker evaluates the normal rollback-enabled rollout.
    result = _check(tmp_path, release_plan, {OLD_TAG: OLD_DIGEST, NEW_TAG: NEW_DIGEST})

    # Then both the forward and recovery image identities require registry verification.
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{OLD_TAG}\t{OLD_DIGEST}\n{NEW_TAG}\t{NEW_DIGEST}\n"


@pytest.mark.parametrize("address", ["aws_iam_role.app", "aws_security_group.task", "aws_s3_bucket.workflow"])
def test_unrelated_resources_cannot_change(tmp_path: Path, release_plan: dict, address: str) -> None:
    # Given a release plan also tries to change identity, network, or storage.
    release_plan["resource_changes"].append({"address": address, "change": {
        "actions": ["update"], "before": {"value": "before"}, "after": {"value": "after"}}})

    # When the checker evaluates the complete saved plan.
    result = _check(tmp_path, release_plan, {NEW_TAG: NEW_DIGEST})

    # Then no accepted image identities escape the rejected plan.
    assert result.returncode == 1
    assert f"outside the production security rollout: {address}" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("field", ["repository", "environment", "cpu"])
def test_image_update_cannot_hide_other_task_changes(tmp_path: Path, release_plan: dict, field: str) -> None:
    # Given an otherwise qualified task revision also changes another behavior.
    after = copy.deepcopy(release_plan["resource_changes"][0]["change"]["after"])
    containers = json.loads(after["container_definitions"])
    if field == "repository":
        containers[0]["image"] = f"unreviewed.example/app:{NEW_TAG}"
    elif field == "environment":
        containers[0]["environment"][1]["value"] = "changed"
    else:
        after["cpu"] = "2048"
    after["container_definitions"] = json.dumps(containers)
    release_plan["resource_changes"][0]["change"]["after"] = after

    # When the actual checker evaluates the altered task.
    result = _check(tmp_path, release_plan, {NEW_TAG: NEW_DIGEST})

    # Then a qualified tag cannot authorize a repository, setting, or resource change.
    assert result.returncode == 1
    assert result.stderr.startswith("Security release plan rejected:")
    assert result.stdout == ""


def _build_change() -> dict:
    before = {"source": [{"type": "GITHUB", "location": "https://github.com/netrias/data_chord.git"}],
              "environment": [{"privileged_mode": True, "environment_variable": []}], "service_role": "same-role"}
    after = copy.deepcopy(before)
    after["source"] = [{"type": "NO_SOURCE", "location": None, "buildspec": "reviewed-inline-bootstrap"}]
    after["environment"][0]["environment_variable"] = [
        {"name": "APPLICATION_REPOSITORY_URL",
         "value": "https://github.com/netrias/data_chord.git", "type": "PLAINTEXT"}]
    return {"address": "aws_codebuild_project.app_image",
            "change": {"actions": ["update"], "before": before, "after": after}}


def test_build_only_update_cannot_select_an_application_image(tmp_path: Path) -> None:
    # Given only the reviewed trust-first build configuration changes.
    plan = {"resource_changes": [_build_change()]}

    # When the real checker runs without an image qualification record.
    result = _check(tmp_path, plan, None)

    # Then it accepts the build-only plan without authorizing an image rollout.
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("field", ["role", "privileges", "repository"])
def test_build_repair_cannot_change_its_access(tmp_path: Path, field: str) -> None:
    # Given a trust repair also attempts to change its role, privileges, or source.
    resource = _build_change()
    after = resource["change"]["after"]
    if field == "role":
        after["service_role"] = "different-role"
    elif field == "privileges":
        after["environment"][0]["privileged_mode"] = False
    else:
        after["environment"][0]["environment_variable"][0]["value"] = "https://other.example/repository.git"

    # When the checker evaluates that serialized configuration.
    result = _check(tmp_path, {"resource_changes": [resource]}, None)

    # Then it rejects the additional change without accepting any artifact.
    assert result.returncode == 1
    assert result.stderr.startswith("Security release plan rejected:")
    assert result.stdout == ""


def test_service_only_task_change_cannot_bypass_qualification(tmp_path: Path, release_plan: dict) -> None:
    # Given a service-only plan selects an unqualified task while disabling automatic rollback.
    release_plan["resource_changes"] = [release_plan["resource_changes"][1]]
    release_plan["resource_changes"][0]["change"]["after"]["task_definition"] = "unqualified-task"

    # When the actual checker reads that plan without any qualification record.
    result = _check(tmp_path, release_plan, None)

    # Then the service cannot bypass the required planned and qualified task.
    assert result.returncode == 1
    assert "Service changes require a planned task" in result.stderr
    assert result.stdout == ""


def test_live_partial_rollout_cannot_use_a_stale_terraform_pointer(tmp_path: Path, release_plan: dict) -> None:
    # Given the live completed task differs from the saved plan's previous task.
    images = {NEW_TAG: NEW_DIGEST, OLD_TAG: OLD_DIGEST}

    # When the checker compares the live task with the saved service and task resource.
    result = _check(tmp_path, release_plan, images, running_task_arn="different-live-task")

    # Then it stops for reconciliation rather than assuming the recovery image.
    assert result.returncode == 1
    assert "Live production differs from the planned previous task" in result.stderr
    assert result.stdout == ""


def test_actual_live_recovery_image_must_be_qualified(tmp_path: Path, release_plan: dict) -> None:
    # Given Terraform's previous image is qualified but the actual completed task image is not.
    release_plan["resource_changes"][1]["change"]["after"]["deployment_circuit_breaker"][0]["rollback"] = True

    # When the checker reads the actual running task's older image identity.
    result = _check(tmp_path, release_plan, {NEW_TAG: NEW_DIGEST, OLD_TAG: OLD_DIGEST},
                    running_image_tag="9ed9d1fa911d")

    # Then it refuses automatic rollback to that actual unqualified image.
    assert result.returncode == 1
    assert "Unqualified release or automatic rollback image" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("selection", ["another-resource", "another-arn"])
def test_service_must_select_the_matching_planned_task(tmp_path: Path, release_plan: dict, selection: str) -> None:
    # Given a qualified task exists but the service selects a different resource or ARN.
    if selection == "another-resource":
        release_plan["configuration"]["root_module"]["resources"][0]["expressions"]["task_definition"]["references"] = [
            "aws_ecs_task_definition.other.arn"]
    else:
        release_plan["resource_changes"][1]["change"]["after"]["task_definition"] = "another-arn"

    # When the actual checker evaluates the service expression and selected task.
    result = _check(tmp_path, release_plan, {NEW_TAG: NEW_DIGEST})

    # Then the presence of a qualified task does not authorize another service target.
    assert result.returncode == 1
    assert result.stderr.startswith("Security release plan rejected:")
    assert result.stdout == ""


@pytest.mark.parametrize("failure", ["source", "checksum", "dirty"])
def test_apply_rejects_changed_receipts_even_under_python_optimization(tmp_path: Path, failure: str) -> None:
    # Given a reviewed-plan receipt is stale, or the working source is dirty despite an old override.
    plan = tmp_path / "reviewed.tfplan"
    plan.write_bytes(b"saved plan bytes")
    record = {"source_commit": NEW_TAG, "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest()}
    if failure == "source":
        record["source_commit"] = OLD_TAG
    elif failure == "checksum":
        record["plan_sha256"] = "0" * 64
    Path(str(plan) + ".receipt.json").write_text(json.dumps(record))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    commands = {
        "aws": "#!/bin/bash\nprintf '084828580051\\n'\n",
        "tofu": '#!/bin/bash\nprintf "tofu %s\\n" "$*" >> "$TEST_COMMAND_LOG"\n',
        "git": (
            '#!/bin/bash\ncase "$*" in\n'
            '*"branch --show-current"*) echo security/bdf725-production ;;\n'
            f'*"rev-parse HEAD"*) echo {NEW_TAG} ;;\n'
            '*"status --porcelain"*) if [[ "$MOCK_DIRTY" == 1 ]]; then echo " M infra/main.tf"; fi ;;\n'
            f'*"ls-remote origin"*) printf "{NEW_TAG}\\trefs/heads/security/bdf725-production\\n" ;;\n'
            '*) exit 2 ;;\nesac\n'
        ),
    }
    for name, content in commands.items():
        executable = binaries / name
        executable.write_text(content)
        executable.chmod(0o755)
    env = os.environ.copy()
    env.update({"PATH": f"{binaries}:{env['PATH']}", "PYTHONOPTIMIZE": "1",
                "DATA_CHORD_SAVED_PLAN": str(plan), "DATA_CHORD_DEPLOY_ALLOW_DIRTY": "1",
                "TEST_COMMAND_LOG": str(log), "MOCK_DIRTY": "1" if failure == "dirty" else "0"})

    # When the real deployment script reaches its apply route under that environment.
    result = subprocess.run(["bash", str(CHECK.parent / "deploy.sh"), "prod", "apply"],
                            env=env, capture_output=True, text=True, check=False)

    # Then source and checksum validation still stop before any saved plan can be applied.
    assert result.returncode == 1, result.stderr
    expected = "uncommitted changes" if failure == "dirty" else "Saved plan or source changed"
    assert expected in result.stderr
    assert " apply " not in log.read_text()


def test_provider_normalization_preserves_task_behavior(tmp_path: Path, release_plan: dict) -> None:
    # Given ECS reports empty defaults while a new revision recomputes metadata.
    change = release_plan["resource_changes"][0]["change"]
    change["before"].update({"ipc_mode": "", "pid_mode": "", "enable_fault_injection": False})
    change["after"].update({"ipc_mode": None, "pid_mode": None, "enable_fault_injection": None})
    change["after_unknown"] = {"enable_fault_injection": True}
    containers = json.loads(change["before"]["container_definitions"])
    containers[0].update({"mountPoints": [], "systemControls": [], "volumesFrom": []})
    change["before"]["container_definitions"] = json.dumps(containers)

    # When the checker compares these equivalent defaults around a qualified image change.
    result = _check(tmp_path, release_plan, {NEW_TAG: NEW_DIGEST})

    # Then provider formatting does not invent a change in application behavior.
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{NEW_TAG}\t{NEW_DIGEST}\n"


@pytest.mark.parametrize("field", ["ipc_mode", "pid_mode", "enable_fault_injection"])
def test_nondefault_task_modes_are_not_normalized_away(tmp_path: Path, release_plan: dict, field: str) -> None:
    # Given a new task tries to enable a namespace mode or fault injection.
    change = release_plan["resource_changes"][0]["change"]
    change["before"][field] = False if field == "enable_fault_injection" else ""
    change["after"][field] = True if field == "enable_fault_injection" else "host"

    # When the checker evaluates that actual behavior change.
    result = _check(tmp_path, release_plan, {NEW_TAG: NEW_DIGEST})

    # Then a provider-normalization allowance cannot hide the changed task mode.
    assert result.returncode == 1
    assert "Task configuration changed outside the image identity" in result.stderr
    assert result.stdout == ""
