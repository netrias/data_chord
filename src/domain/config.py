"""
Build-level configuration for data model integration.

Centralized here to allow fail-fast validation at Docker build time.
"""

from __future__ import annotations

import os


class ConfigurationError(Exception):
    pass


_NETRIAS_API_KEY_VAR = "NETRIAS_API_KEY"
_DATA_CHORD_STORAGE_VAR = "DATA_CHORD_STORAGE"
_DATA_CHORD_WORKFLOW_STORAGE_DIR_VAR = "DATA_CHORD_WORKFLOW_STORAGE_DIR"
_DATA_CHORD_S3_BUCKET_VAR = "DATA_CHORD_S3_BUCKET"
_DATA_CHORD_S3_PREFIX_VAR = "DATA_CHORD_S3_PREFIX"
_DEFAULT_STORAGE_BACKEND = "local"


def get_netrias_api_key() -> str | None:
    return os.getenv(_NETRIAS_API_KEY_VAR)


def get_storage_backend() -> str:
    return os.getenv(_DATA_CHORD_STORAGE_VAR, _DEFAULT_STORAGE_BACKEND).strip().lower()


def get_workflow_storage_dir() -> str | None:
    return os.getenv(_DATA_CHORD_WORKFLOW_STORAGE_DIR_VAR)


def get_workflow_s3_bucket() -> str | None:
    return os.getenv(_DATA_CHORD_S3_BUCKET_VAR)


def get_workflow_s3_prefix() -> str:
    return os.getenv(_DATA_CHORD_S3_PREFIX_VAR, "").strip()


def validate_required_config() -> None:
    """Call at startup or Docker build to fail fast on missing config."""
    if not get_netrias_api_key():
        raise ConfigurationError(f"{_NETRIAS_API_KEY_VAR} environment variable is required")
