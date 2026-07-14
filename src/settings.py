"""
Build-level configuration for data model integration.

Centralized here to allow fail-fast validation at Docker build time.
"""

from __future__ import annotations

import os
from enum import StrEnum


class ConfigurationError(Exception):
    pass


class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"


_NETRIAS_API_KEY_VAR = "NETRIAS_API_KEY"
_DATA_CHORD_STORAGE_VAR = "DATA_CHORD_STORAGE"
_DATA_CHORD_UPLOAD_DIR_VAR = "DATA_CHORD_UPLOAD_DIR"
_DATA_CHORD_WORKFLOW_STORAGE_DIR_VAR = "DATA_CHORD_WORKFLOW_STORAGE_DIR"
_DATA_CHORD_S3_BUCKET_VAR = "DATA_CHORD_S3_BUCKET"
_DATA_CHORD_S3_PREFIX_VAR = "DATA_CHORD_S3_PREFIX"
_DATA_CHORD_NETRIAS_ENVIRONMENT_VAR = "DATA_CHORD_NETRIAS_ENVIRONMENT"
_DATA_CHORD_NETRIAS_HARMONIZATION_URL_VAR = "DATA_CHORD_NETRIAS_HARMONIZATION_URL"
_DATA_CHORD_NETRIAS_TIMEOUT_SECONDS_VAR = "DATA_CHORD_NETRIAS_TIMEOUT_SECONDS"
_DATA_CHORD_ALB_ARN_VAR = "DATA_CHORD_ALB_ARN"
_DEFAULT_STORAGE_BACKEND = StorageBackend.LOCAL
_DEFAULT_NETRIAS_ENVIRONMENT = "staging"
_VALID_NETRIAS_ENVIRONMENTS = frozenset({"prod", "staging"})


def get_netrias_api_key() -> str | None:
    return os.getenv(_NETRIAS_API_KEY_VAR)


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


def get_netrias_environment_name() -> str:
    raw_environment = os.getenv(_DATA_CHORD_NETRIAS_ENVIRONMENT_VAR, _DEFAULT_NETRIAS_ENVIRONMENT)
    environment = raw_environment.strip().lower()
    if environment not in _VALID_NETRIAS_ENVIRONMENTS:
        raise ConfigurationError(
            f"{_DATA_CHORD_NETRIAS_ENVIRONMENT_VAR} must be one of: "
            f"{', '.join(sorted(_VALID_NETRIAS_ENVIRONMENTS))}"
        )
    return environment


def get_netrias_harmonization_url() -> str | None:
    raw_url = os.getenv(_DATA_CHORD_NETRIAS_HARMONIZATION_URL_VAR)
    if raw_url is None:
        return None
    url = raw_url.strip()
    return url or None


def get_netrias_timeout_seconds() -> float | None:
    raw_timeout = os.getenv(_DATA_CHORD_NETRIAS_TIMEOUT_SECONDS_VAR)
    if raw_timeout is None or not raw_timeout.strip():
        return None
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise ConfigurationError(f"{_DATA_CHORD_NETRIAS_TIMEOUT_SECONDS_VAR} must be a number") from exc
    if timeout <= 0:
        raise ConfigurationError(f"{_DATA_CHORD_NETRIAS_TIMEOUT_SECONDS_VAR} must be positive")
    return timeout


def get_expected_alb_arn() -> str | None:
    raw_arn = os.getenv(_DATA_CHORD_ALB_ARN_VAR)
    if raw_arn is None:
        return None
    arn = raw_arn.strip()
    return arn or None


def validate_required_config() -> None:
    """Call at startup or Docker build to fail fast on missing config."""
    if not get_netrias_api_key():
        raise ConfigurationError(f"{_NETRIAS_API_KEY_VAR} environment variable is required")
