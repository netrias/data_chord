#!/usr/bin/env python3
"""Convert one AWS CodeBuild response into a safe deployment status."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_FAILURE_STATUSES = {"FAILED", "FAULT", "STOPPED", "TIMED_OUT"}


class CodeBuildResponseError(ValueError):
    """AWS did not return one usable build."""


def _object(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CodeBuildResponseError(f"AWS returned no valid {description}")
    return {str(key): item for key, item in value.items()}


def _text(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _failed_phase(build: dict[str, object]) -> tuple[str, str]:
    phases = build.get("phases")
    if isinstance(phases, list):
        for raw_phase in phases:
            phase = _object(raw_phase, "CodeBuild phase")
            if phase.get("phaseStatus") not in _FAILURE_STATUSES:
                continue
            phase_name = _text(phase.get("phaseType"), "UNKNOWN")
            contexts = phase.get("contexts")
            if isinstance(contexts, list):
                for raw_context in contexts:
                    context = _object(raw_context, "CodeBuild failure context")
                    message = context.get("message")
                    if isinstance(message, str) and message:
                        return phase_name, message
            return phase_name, "no failure message was returned"
    return _text(build.get("currentPhase"), "UNKNOWN"), "no failure message was returned"


def _build(document: dict[str, object], build_id: str) -> dict[str, object]:
    missing = document.get("buildsNotFound")
    if isinstance(missing, list) and build_id in missing:
        raise CodeBuildResponseError(f"the requested build {build_id} was not returned")
    builds = document.get("builds")
    if not isinstance(builds, list) or len(builds) != 1:
        raise CodeBuildResponseError(f"the requested build {build_id} was not returned")
    build = _object(builds[0], "CodeBuild record")
    if build.get("id") != build_id:
        raise CodeBuildResponseError(f"the requested build {build_id} was not returned")
    return build


def _load(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodeBuildResponseError("AWS returned invalid CodeBuild JSON") from exc
    return _object(raw, "CodeBuild response")


def _main(arguments: list[str]) -> int:
    if len(arguments) != 4:
        print("CodeBuild status requires a response, build ID, target, and stage", file=sys.stderr)
        return 2
    path = Path(arguments[0])
    build_id, target, stage = arguments[1:]
    next_action = f"The current plan cannot be reused. Next: just plan {target} {stage}"
    try:
        build = _build(_load(path), build_id)
    except CodeBuildResponseError as exc:
        print(
            f"Could not inspect CodeBuild {build_id}. Status: UNKNOWN. Phase: UNKNOWN. "
            f"AWS: {exc}. {next_action}",
            file=sys.stderr,
        )
        return 2
    status = _text(build.get("buildStatus"), "UNKNOWN")
    current_phase = _text(build.get("currentPhase"), "UNKNOWN")
    if status in _FAILURE_STATUSES:
        try:
            phase, message = _failed_phase(build)
        except CodeBuildResponseError as exc:
            print(
                f"CodeBuild {build_id} failed. Status: {status}. Phase: {current_phase}. "
                f"AWS: {exc}. {next_action}",
                file=sys.stderr,
            )
            return 2
        print(
            f"CodeBuild {build_id} failed in {phase}. Status: {status}. "
            f"AWS: {message}. {next_action}",
            file=sys.stderr,
        )
        return 2
    if status == "UNKNOWN":
        print(
            f"CodeBuild {build_id} returned no status. Phase: {current_phase}. "
            f"AWS: no failure message was returned. {next_action}",
            file=sys.stderr,
        )
        return 2
    print(status, current_phase)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
