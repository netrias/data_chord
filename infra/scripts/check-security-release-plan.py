"""Reject production changes outside the security build and qualified image rollout.

The qualification record maps immutable image tags to verified registry digests.
It is an operator evidence artifact, not permission to change other resources.
The caller checks those digests against ECR before applying the saved plan.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path


def _changed_fields(before: dict, after: dict) -> set[str]:
    return {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


def _image_tag(containers: str) -> str:
    apps = [item for item in json.loads(containers) if item["name"] == "app"]
    if len(apps) != 1:
        raise ValueError("Expected exactly one app container")
    return apps[0]["image"].rsplit(":", 1)[1]


def _without_image_identity(containers: str) -> list[dict]:
    result = copy.deepcopy(json.loads(containers))
    for container in result:
        for key in ("mountPoints", "systemControls", "volumesFrom"):
            if container.get(key) == []:
                container.pop(key)
        if container["name"] == "app":
            container["image"] = container["image"].rsplit(":", 1)[0] + ":QUALIFIED_IMAGE"
            for variable in container.get("environment", []):
                if variable["name"] == "DATA_CHORD_ASSET_VERSION":
                    variable["value"] = "QUALIFIED_IMAGE"
    return result


def _qualified_images(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    images = json.loads(path.read_text())["qualified_images"]
    if not isinstance(images, dict) or not images:
        raise ValueError("Qualification record must name verified image tags and digests")
    for tag, digest in images.items():
        if not re.fullmatch(r"[0-9a-f]{40}", tag) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Qualified images require full source commits and registry digests")
    return images


def _check_build(change: dict) -> None:
    before, after = change["before"], change["after"]
    if change["actions"] != ["update"] or _changed_fields(before, after) - {"source", "cache", "environment"}:
        raise ValueError("Only CodeBuild source, cache, and environment may change")
    old_env, new_env = before["environment"][0], after["environment"][0]
    if _changed_fields(old_env, new_env) - {"environment_variable"}:
        raise ValueError("CodeBuild worker privileges or identity changed")
    variables = {v["name"]: v for v in new_env["environment_variable"]}
    old_variables = {v["name"]: v for v in old_env["environment_variable"]}
    if any(variables.get(name) != value for name, value in old_variables.items()):
        raise ValueError("Existing build variables must be preserved")
    if set(variables) - old_variables.keys() - {"APPLICATION_REPOSITORY_URL"}:
        raise ValueError("Unexpected new build variable")
    repository = variables.get("APPLICATION_REPOSITORY_URL")
    expected_repository = before["source"][0]["location"]
    if expected_repository is None:
        expected_repository = old_variables["APPLICATION_REPOSITORY_URL"]["value"]
    if repository is not None and repository["value"] != expected_repository:
        raise ValueError("The public application source repository must be preserved")
    if after["source"][0]["type"] != "NO_SOURCE":
        raise ValueError("The security build must retain the trust-first source bootstrap")


def _check_task(change: dict) -> str:
    before, after = change["before"], change["after"]
    if change["actions"] not in (["delete", "create"], ["create", "delete"]):
        raise ValueError("Expected a normal task-definition revision")
    fields = _changed_fields(before, after)
    # The provider normalizes absent Fargate namespace modes and recomputes
    # fault-injection metadata on a task revision; none enables that feature.
    for field in ("ipc_mode", "pid_mode"):
        if before.get(field) == "" and after.get(field) is None:
            fields.discard(field)
    fault_recomputed = change.get("after_unknown", {}).get("enable_fault_injection") is True
    if before.get("enable_fault_injection") is False and fault_recomputed:
        fields.discard("enable_fault_injection")
    if fields - {"container_definitions", "arn", "arn_without_revision", "id", "revision"}:
        raise ValueError("Task configuration changed outside the image identity")
    old_containers = _without_image_identity(before["container_definitions"])
    if old_containers != _without_image_identity(after["container_definitions"]):
        raise ValueError("Container behavior changed outside image identity")
    return _image_tag(after["container_definitions"])


@dataclass(frozen=True)
class _RunningDeployment:
    task_arn: str
    image_tag: str


def _running_deployment(service_path: Path | None, task_path: Path | None) -> _RunningDeployment | None:
    if service_path is None or task_path is None:
        return None
    service = json.loads(service_path.read_text())
    task = json.loads(task_path.read_text())
    deployments = service["deployments"]
    if len(deployments) != 1:
        raise ValueError("Production must have one stable completed deployment before release")
    deployment = deployments[0]
    if (deployment["status"] != "PRIMARY" or deployment["rolloutState"] != "COMPLETED"
            or deployment["desiredCount"] < 1 or deployment["runningCount"] != deployment["desiredCount"]
            or deployment["pendingCount"] != 0):
        raise ValueError("Production is not stable; reconcile the existing rollout first")
    arn = task["taskDefinitionArn"]
    if service["taskDefinition"] != arn or deployment["taskDefinition"] != arn:
        raise ValueError("Live production task identities disagree")
    return _RunningDeployment(arn, _image_tag(json.dumps(task["containerDefinitions"])))


def _service_uses_planned_task(plan: dict, change: dict, task: dict) -> None:
    resources = plan["configuration"]["root_module"]["resources"]
    service = next(item for item in resources if item["address"] == "aws_ecs_service.app")
    references = set(service["expressions"]["task_definition"]["references"])
    allowed = {"aws_ecs_task_definition.app", "aws_ecs_task_definition.app.arn"}
    if not references or references - allowed:
        raise ValueError("Service must refer only to the planned app task definition")
    selected = change["after"]["task_definition"]
    if selected is None:
        if change.get("after_unknown", {}).get("task_definition") is not True:
            raise ValueError("Service task reference is neither known nor planned")
    elif selected != task["change"]["after"]["arn"]:
        raise ValueError("Service does not select the planned task definition")


def _check_service(change: dict, task: dict, running: _RunningDeployment) -> set[str]:
    before, after = change["before"], change["after"]
    allowed = {"task_definition", "deployment_circuit_breaker"}
    if change["actions"] != ["update"] or _changed_fields(before, after) - allowed:
        raise ValueError("Only the service task revision and rollback switch may change")
    if before["task_definition"] != running.task_arn or task["change"]["before"]["arn"] != running.task_arn:
        raise ValueError("Live production differs from the planned previous task; reconcile first")
    if after["deployment_circuit_breaker"][0]["enable"] is not True:
        raise ValueError("The deployment circuit breaker must remain enabled")
    return {running.image_tag} if after["deployment_circuit_breaker"][0]["rollback"] else set()


def _check_plan(plan: dict, images: dict[str, str], running: _RunningDeployment | None) -> set[str]:
    changes = [item for item in plan.get("resource_changes", [])
               if item["change"]["actions"] not in (["no-op"], ["read"])]
    task = next((item for item in changes if item["address"] == "aws_ecs_task_definition.app"), None)
    tags: set[str] = set()
    for item in changes:
        address, change = item["address"], item["change"]
        if not isinstance(change["before"], dict) or not isinstance(change["after"], dict):
            raise ValueError(f"Creation or deletion is not permitted: {address}")
        if address == "aws_codebuild_project.app_image":
            _check_build(change)
        elif address == "aws_ecs_task_definition.app":
            tags.add(_check_task(change))
        elif address == "aws_ecs_service.app":
            if task is None or running is None:
                raise ValueError("Service changes require a planned task and live stable deployment evidence")
            tags.update(_check_service(change, task, running))
            _service_uses_planned_task(plan, change, task)
        else:
            raise ValueError(f"Resource is outside the production security rollout: {address}")
    if tags - images.keys():
        raise ValueError(f"Unqualified release or automatic rollback image: {sorted(tags - images.keys())}")
    return tags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--running-service", type=Path)
    parser.add_argument("--running-task", type=Path)
    args = parser.parse_args()
    try:
        images = _qualified_images(args.qualification)
        running = _running_deployment(args.running_service, args.running_task)
        tags = _check_plan(json.loads(args.plan.read_text()), images, running)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise SystemExit(f"Security release plan rejected: {exc}") from exc
    for tag in sorted(tags):
        print(f"{tag}\t{images[tag]}")


if __name__ == "__main__":
    main()
