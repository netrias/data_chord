"""Runtime settings with fail-fast validation at application startup."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path


class ConfigurationError(Exception):
    pass


class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class RuntimeProfile(StrEnum):
    HOSTED = "hosted"
    PORTABLE = "portable"


_DATA_CHORD_PROFILE_VAR = "DATA_CHORD_PROFILE"
_DATA_CHORD_DATA_DIR_VAR = "DATA_CHORD_DATA_DIR"
_DATA_CHORD_STORAGE_VAR = "DATA_CHORD_STORAGE"
_DATA_CHORD_UPLOAD_DIR_VAR = "DATA_CHORD_UPLOAD_DIR"
_DATA_CHORD_WORKFLOW_STORAGE_DIR_VAR = "DATA_CHORD_WORKFLOW_STORAGE_DIR"
_DATA_CHORD_S3_BUCKET_VAR = "DATA_CHORD_S3_BUCKET"
_DATA_CHORD_S3_PREFIX_VAR = "DATA_CHORD_S3_PREFIX"
_DATA_CHORD_ALB_ARN_VAR = "DATA_CHORD_ALB_ARN"
_DATA_CHORD_AGENTIC_WORKERS_VAR = "DATA_CHORD_AGENTIC_WORKERS"
_DATA_CHORD_REFERENCE_TABLE_VAR = "DATA_CHORD_REFERENCE_TABLE"
_DATA_CHORD_HARMONIZATION_CACHE_TABLE_VAR = "DATA_CHORD_HARMONIZATION_CACHE_TABLE"
_DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE_VAR = "DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE"
_AWS_REGION_VAR = "AWS_REGION"
_DEFAULT_STORAGE_BACKEND = StorageBackend.LOCAL
_DEFAULT_RUNTIME_PROFILE = RuntimeProfile.HOSTED
_DEFAULT_AGENTIC_WORKERS = 100
_DEFAULT_AWS_REGION = "us-east-2"
_DEFAULT_DATA_DIR = Path("/data")


def get_runtime_profile() -> RuntimeProfile:
    raw_profile = os.getenv(_DATA_CHORD_PROFILE_VAR, _DEFAULT_RUNTIME_PROFILE.value).strip().lower()
    try:
        return RuntimeProfile(raw_profile)
    except ValueError as exc:
        valid_profiles = ", ".join(profile.value for profile in RuntimeProfile)
        raise ConfigurationError(f"{_DATA_CHORD_PROFILE_VAR} must be one of: {valid_profiles}") from exc


def get_data_dir() -> Path:
    path = Path(os.getenv(_DATA_CHORD_DATA_DIR_VAR, str(_DEFAULT_DATA_DIR))).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(f"{_DATA_CHORD_DATA_DIR_VAR} must be an absolute path")
    return path


def get_reference_database_path() -> Path:
    return get_data_dir() / "standards.sqlite"


def get_agentic_workers() -> int:
    raw_workers = os.getenv(_DATA_CHORD_AGENTIC_WORKERS_VAR, str(_DEFAULT_AGENTIC_WORKERS))
    try:
        workers = int(raw_workers)
    except ValueError as exc:
        raise ConfigurationError(f"{_DATA_CHORD_AGENTIC_WORKERS_VAR} must be an integer") from exc
    if workers < 1:
        raise ConfigurationError(f"{_DATA_CHORD_AGENTIC_WORKERS_VAR} must be positive")
    if workers > 100:
        raise ConfigurationError(f"{_DATA_CHORD_AGENTIC_WORKERS_VAR} must not exceed 100")
    return workers


def get_aws_region() -> str:
    region = os.getenv(_AWS_REGION_VAR, _DEFAULT_AWS_REGION).strip()
    if not region:
        raise ConfigurationError(f"{_AWS_REGION_VAR} must not be empty")
    return region


def get_storage_backend() -> StorageBackend:
    raw_backend = os.getenv(_DATA_CHORD_STORAGE_VAR, _DEFAULT_STORAGE_BACKEND.value).strip().lower()
    try:
        return StorageBackend(raw_backend)
    except ValueError as exc:
        valid_backends = ", ".join(backend.value for backend in StorageBackend)
        raise ConfigurationError(f"{_DATA_CHORD_STORAGE_VAR} must be one of: {valid_backends}") from exc


def get_upload_dir() -> str | None:
    return os.getenv(_DATA_CHORD_UPLOAD_DIR_VAR)


def get_workflow_storage_dir() -> str | None:
    return os.getenv(_DATA_CHORD_WORKFLOW_STORAGE_DIR_VAR)


def get_workflow_s3_bucket() -> str | None:
    return os.getenv(_DATA_CHORD_S3_BUCKET_VAR)


def get_workflow_s3_prefix() -> str:
    return os.getenv(_DATA_CHORD_S3_PREFIX_VAR, "").strip()


def get_reference_table_name() -> str:
    table_name = os.getenv(_DATA_CHORD_REFERENCE_TABLE_VAR, "").strip()
    if not table_name:
        raise ConfigurationError(f"{_DATA_CHORD_REFERENCE_TABLE_VAR} environment variable is required")
    return table_name


def get_harmonization_cache_table_name() -> str:
    table_name = os.getenv(_DATA_CHORD_HARMONIZATION_CACHE_TABLE_VAR, "").strip()
    if not table_name:
        raise ConfigurationError(
            f"{_DATA_CHORD_HARMONIZATION_CACHE_TABLE_VAR} environment variable is required"
        )
    return table_name


def get_cde_recommendation_cache_table_name() -> str:
    table_name = os.getenv(_DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE_VAR, "").strip()
    if not table_name:
        raise ConfigurationError(
            f"{_DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE_VAR} environment variable is required"
        )
    return table_name


def get_expected_alb_arn() -> str | None:
    raw_arn = os.getenv(_DATA_CHORD_ALB_ARN_VAR)
    if raw_arn is None:
        return None
    arn = raw_arn.strip()
    return arn or None


def validate_required_config() -> None:
    """Validate all runtime configuration before service clients are created."""
    profile = get_runtime_profile()
    if profile is RuntimeProfile.PORTABLE:
        database = get_reference_database_path()
        if not database.is_file():
            raise ConfigurationError(f"Portable reference database does not exist: {database}")
    else:
        get_reference_table_name()
        get_harmonization_cache_table_name()
        get_cde_recommendation_cache_table_name()

        storage_backend = get_storage_backend()
        if storage_backend is StorageBackend.S3:
            bucket = get_workflow_s3_bucket()
            if bucket is None or not bucket.strip():
                raise ConfigurationError(
                    f"{_DATA_CHORD_S3_BUCKET_VAR} is required when "
                    f"{_DATA_CHORD_STORAGE_VAR}={StorageBackend.S3.value}"
                )

    get_agentic_workers()
    get_aws_region()
