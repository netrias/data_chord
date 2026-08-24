"""Operator-visible startup configuration behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel
from src.integrations.sqlite_reference_data import SqliteReferenceDataImporter
from src.paths import PROJECT_ROOT

_RUNTIME_CONFIG_NAMES = (
    "DATA_CHORD_AGENTIC_WORKERS",
    "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE",
    "DATA_CHORD_MODE",
    "DATA_CHORD_HARMONIZATION_CACHE_TABLE",
    "DATA_CHORD_IDENTITY_SOURCE",
    "DATA_CHORD_DATA_DIR",
    "DATA_CHORD_PROFILE",
    "DATA_CHORD_REFERENCE_TABLE",
    "DATA_CHORD_S3_BUCKET",
    "DATA_CHORD_STORAGE",
    "DATA_CHORD_ALB_ARN",
    "DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB",
    "DEV_MODE",
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
        ({"DATA_CHORD_PROFILE": "unknown"}, "DATA_CHORD_PROFILE must be one of"),
        ({"DATA_CHORD_MODE": "unknown"}, "DATA_CHORD_MODE must be one of"),
        (
            {
                "DATA_CHORD_PROFILE": "portable",
                "DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB": "zero",
            },
            "DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB must be a positive number",
        ),
    ],
)
def test_invalid_runtime_configuration_stops_application_startup(
    settings: dict[str, str],
    expected_error: str,
) -> None:
    # Given invalid required runtime settings.

    # When the app starts.
    result = _run_import("backend.app.main", settings)

    # Then it stops with a clear error.
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
            "DATA_CHORD_IDENTITY_SOURCE": "trusted_proxy",
        },
        {
            "DATA_CHORD_REFERENCE_TABLE": "reference",
            "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
            "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
            "DATA_CHORD_STORAGE": "s3",
            "DATA_CHORD_S3_BUCKET": "data-chord-test",
            "DATA_CHORD_IDENTITY_SOURCE": "trusted_proxy",
        },
        {
            "DATA_CHORD_REFERENCE_TABLE": "reference",
            "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
            "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
            "DATA_CHORD_STORAGE": "s3",
            "DATA_CHORD_S3_BUCKET": "data-chord-test",
            "DATA_CHORD_IDENTITY_SOURCE": "signed_alb",
            "DATA_CHORD_ALB_ARN": (
                "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/data-chord/abc123"
            ),
        },
    ],
)
def test_valid_runtime_configuration_starts_application(settings: dict[str, str]) -> None:
    # Given a reference table and valid storage, when the app starts, then startup succeeds.
    result = _run_import("backend.app.main", settings)

    assert result.returncode == 0, result.stderr


def test_local_development_can_use_shared_identity() -> None:
    # Given local development has all hosted data settings.
    settings = {
        "DATA_CHORD_REFERENCE_TABLE": "reference",
        "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
        "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
        "DATA_CHORD_IDENTITY_SOURCE": "shared",
        "DEV_MODE": "true",
    }

    # When the local application starts, then shared identity is accepted.
    result = _run_import("backend.app.main", settings)

    assert result.returncode == 0, result.stderr


def test_portable_runtime_starts_without_aws_data_service_settings(tmp_path: Path) -> None:
    # Given a portable volume with an initialized standards database.
    _initialize_portable_standards(tmp_path / "standards.sqlite")

    # When the application starts with only the portable profile and data directory.
    result = _run_import(
        "backend.app.main",
        {
            "DATA_CHORD_PROFILE": "portable",
            "DATA_CHORD_DATA_DIR": str(tmp_path),
            "DATA_CHORD_IDENTITY_SOURCE": "shared",
            "AWS_REGION": "us-east-2",
        },
    )

    # Then startup succeeds without DynamoDB or S3 settings.
    assert result.returncode == 0, result.stderr


def test_demo_mode_requires_the_portable_runtime_profile() -> None:
    # Given demo behavior is requested without the portable runtime profile.

    # When the application starts.
    result = _run_import(
        "backend.app.main",
        {
            "DATA_CHORD_MODE": "demo",
            "DATA_CHORD_REFERENCE_TABLE": "reference",
            "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
            "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
        },
    )

    # Then startup rejects the invalid combination before serving requests.
    assert result.returncode != 0
    assert "DATA_CHORD_MODE=demo requires DATA_CHORD_PROFILE=portable" in result.stderr


def test_portable_runtime_requires_a_loaded_standards_database(tmp_path: Path) -> None:
    # Given a portable data directory without a standards database.
    assert (tmp_path / "standards.sqlite").exists() is False

    # When the application starts.
    result = _run_import(
        "backend.app.main",
        {"DATA_CHORD_PROFILE": "portable", "DATA_CHORD_DATA_DIR": str(tmp_path)},
    )

    # Then it reports the missing local requirement.
    assert result.returncode != 0
    assert "Portable reference database does not exist" in result.stderr


def test_portable_runtime_rejects_an_unusable_standards_database(tmp_path: Path) -> None:
    # Given a portable volume with a zero-byte standards file.
    (tmp_path / "standards.sqlite").touch()

    # When the application starts.
    result = _run_import(
        "backend.app.main",
        {"DATA_CHORD_PROFILE": "portable", "DATA_CHORD_DATA_DIR": str(tmp_path)},
    )

    # Then startup reports that the database is unusable.
    assert result.returncode != 0
    assert "Portable reference database is not usable" in result.stderr


def test_portable_runtime_rejects_an_empty_standards_catalog(tmp_path: Path) -> None:
    # Given a valid portable database with no published model versions.
    SqliteReferenceDataImporter(tmp_path / "standards.sqlite").import_models([])

    # When the application starts.
    result = _run_import(
        "backend.app.main",
        {"DATA_CHORD_PROFILE": "portable", "DATA_CHORD_DATA_DIR": str(tmp_path)},
    )

    # Then startup reports that the catalog has no usable standards.
    assert result.returncode != 0
    assert "Portable reference database contains no model versions" in result.stderr


def test_importing_application_package_does_not_start_application() -> None:
    # Given no runtime settings, when only the package is imported, then the application does not start.
    result = _run_import("backend.app", {})

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("settings", "expected_error"),
    [
        (
            {
                "DATA_CHORD_PROFILE": "hosted",
                "DATA_CHORD_REFERENCE_TABLE": "reference",
                "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
                "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
                "DATA_CHORD_IDENTITY_SOURCE": "shared",
            },
            "hosted profile requires trusted_proxy or signed_alb identity",
        ),
        (
            {
                "DATA_CHORD_PROFILE": "portable",
                "DATA_CHORD_IDENTITY_SOURCE": "trusted_proxy",
            },
            "portable profile requires shared identity",
        ),
        (
            {
                "DATA_CHORD_PROFILE": "hosted",
                "DATA_CHORD_REFERENCE_TABLE": "reference",
                "DATA_CHORD_HARMONIZATION_CACHE_TABLE": "cache",
                "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE": "cde-cache",
                "DATA_CHORD_IDENTITY_SOURCE": "signed_alb",
            },
            "DATA_CHORD_ALB_ARN is required when DATA_CHORD_IDENTITY_SOURCE=signed_alb",
        ),
    ],
)
def test_unsafe_profile_and_identity_combinations_stop_startup(
    settings: dict[str, str],
    expected_error: str,
) -> None:
    # Given a data profile and identity source do not describe a supported offer.

    # When the app starts.
    result = _run_import("backend.app.main", settings)

    # Then startup rejects the unsafe combination with one exact error.
    assert result.returncode != 0
    assert expected_error in result.stderr


def _initialize_portable_standards(database: Path) -> None:
    model = ReferenceModel(
        version=DataModelVersionReference("model", "1"),
        label="Model",
        catalog=CdeCatalog.from_cdes(
            [
                CDEInfo(None, "field", None, CdeType.PASSTHROUGH),
            ]
        ),
        pvs=CdePvCatalog.from_mapping({"field": frozenset()}),
    )
    SqliteReferenceDataImporter(database).import_models([model])
