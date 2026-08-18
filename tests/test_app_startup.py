"""Operator-visible startup configuration behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.paths import PROJECT_ROOT

_RUNTIME_CONFIG_NAMES = (
    "DATA_CHORD_AGENTIC_WORKERS",
    "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE",
    "DATA_CHORD_HARMONIZATION_CACHE_TABLE",
    "DATA_CHORD_REFERENCE_TABLE",
    "DATA_CHORD_S3_BUCKET",
    "DATA_CHORD_STORAGE",
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
        ({}, "DATA_CHORD_REFERENCE_TABLE environment variable is required"),
        (
            {"DATA_CHORD_REFERENCE_TABLE": "reference"},
            "DATA_CHORD_HARMONIZATION_CACHE_TABLE environment variable is required",
        ),
        (
            {
                "DATA_CHORD_REFERENCE_TABLE": "reference",
                "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
            },
            "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE environment variable is required",
        ),
        (
            {
                "DATA_CHORD_REFERENCE_TABLE": "reference",
                "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
                "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
                "DATA_CHORD_STORAGE": "unknown",
            },
            "DATA_CHORD_STORAGE must be one of",
        ),
        (
            {
                "DATA_CHORD_REFERENCE_TABLE": "reference",
                "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
                "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
                "DATA_CHORD_STORAGE": "s3",
            },
            "DATA_CHORD_S3_BUCKET is required",
        ),
        (
            {
                "DATA_CHORD_REFERENCE_TABLE": "reference",
                "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
                "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
                "DATA_CHORD_AGENTIC_WORKERS": "0",
            },
            "DATA_CHORD_AGENTIC_WORKERS must be positive",
        ),
        (
            {
                "DATA_CHORD_REFERENCE_TABLE": "reference",
                "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
                "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
                "DATA_CHORD_AGENTIC_WORKERS": "101",
            },
            "DATA_CHORD_AGENTIC_WORKERS must not exceed 100",
        ),
    ],
)
def test_invalid_runtime_configuration_stops_application_startup(
    settings: dict[str, str],
    expected_error: str,
) -> None:
    # Given invalid required runtime settings, when the app starts, then it stops with a clear error.
    result = _run_import("backend.app.main", settings)

    assert result.returncode != 0
    assert expected_error in result.stderr


@pytest.mark.parametrize(
    "settings",
    [
        {
            "DATA_CHORD_REFERENCE_TABLE": "reference",
            "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
            "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
            "DATA_CHORD_STORAGE": "local",
        },
        {
            "DATA_CHORD_REFERENCE_TABLE": "reference",
            "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
            "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
            "DATA_CHORD_STORAGE": "s3",
            "DATA_CHORD_S3_BUCKET": "data-chord-test",
        },
    ],
)
def test_valid_runtime_configuration_starts_application(settings: dict[str, str]) -> None:
    # Given a reference table and valid storage, when the app starts, then startup succeeds.
    result = _run_import("backend.app.main", settings)

    assert result.returncode == 0, result.stderr


def test_importing_application_package_does_not_start_application() -> None:
    # Given no runtime settings, when only the package is imported, then the application does not start.
    result = _run_import("backend.app", {})

    assert result.returncode == 0, result.stderr
