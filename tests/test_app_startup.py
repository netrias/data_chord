"""Operator-visible startup configuration behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.paths import PROJECT_ROOT

_RUNTIME_CONFIG_NAMES = (
    "DATA_CHORD_NETRIAS_ENVIRONMENT",
    "DATA_CHORD_NETRIAS_TIMEOUT_SECONDS",
    "DATA_CHORD_S3_BUCKET",
    "DATA_CHORD_STORAGE",
    "NETRIAS_API_KEY",
)


def _run_import(module: str, settings: dict[str, str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in _RUNTIME_CONFIG_NAMES:
        environment.pop(name, None)
    environment.update(settings)
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=Path(PROJECT_ROOT),
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


@pytest.mark.parametrize(
    ("settings", "expected_error"),
    [
        ({"NETRIAS_API_KEY": "   "}, "NETRIAS_API_KEY environment variable is required"),
        (
            {"NETRIAS_API_KEY": "test", "DATA_CHORD_STORAGE": "unknown"},
            "DATA_CHORD_STORAGE must be one of",
        ),
        (
            {"NETRIAS_API_KEY": "test", "DATA_CHORD_STORAGE": "s3"},
            "DATA_CHORD_S3_BUCKET is required",
        ),
        (
            {"NETRIAS_API_KEY": "test", "DATA_CHORD_NETRIAS_ENVIRONMENT": "qa"},
            "DATA_CHORD_NETRIAS_ENVIRONMENT must be one of",
        ),
        (
            {"NETRIAS_API_KEY": "test", "DATA_CHORD_NETRIAS_TIMEOUT_SECONDS": "fast"},
            "DATA_CHORD_NETRIAS_TIMEOUT_SECONDS must be a number",
        ),
        (
            {"NETRIAS_API_KEY": "test", "DATA_CHORD_NETRIAS_TIMEOUT_SECONDS": "0"},
            "DATA_CHORD_NETRIAS_TIMEOUT_SECONDS must be positive",
        ),
    ],
)
def test_invalid_runtime_configuration_stops_application_startup(
    settings: dict[str, str],
    expected_error: str,
) -> None:
    result = _run_import("backend.app.main", settings)

    assert result.returncode != 0
    assert expected_error in result.stderr


@pytest.mark.parametrize(
    "settings",
    [
        {"NETRIAS_API_KEY": "test", "DATA_CHORD_STORAGE": "local"},
        {
            "NETRIAS_API_KEY": "test",
            "DATA_CHORD_STORAGE": "s3",
            "DATA_CHORD_S3_BUCKET": "data-chord-test",
        },
    ],
)
def test_valid_runtime_configuration_starts_application(settings: dict[str, str]) -> None:
    result = _run_import("backend.app.main", settings)

    assert result.returncode == 0, result.stderr


def test_importing_application_package_does_not_start_application() -> None:
    result = _run_import("backend.app", {"NETRIAS_API_KEY": "   "})

    assert result.returncode == 0, result.stderr
