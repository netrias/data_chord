"""
Build-level configuration for data model integration.

why: Centralize required configuration with fail-fast validation.
"""

from __future__ import annotations

import os


class ConfigurationError(Exception):
    """why: Distinguish config errors from runtime errors."""


_DATA_MODEL_KEY_VAR = "DATA_MODEL_KEY"


def get_data_model_key() -> str:
    """why: Return configured data model key, fail if missing."""
    key = os.getenv(_DATA_MODEL_KEY_VAR)
    if not key:
        raise ConfigurationError(f"{_DATA_MODEL_KEY_VAR} environment variable is required")
    return key


def get_data_model_store_api_key() -> str | None:
    """why: Return API key for Data Model Store.

    Falls back to NETRIAS_API_KEY if DATA_MODEL_STORE_API_KEY not set.
    """
    return os.getenv("DATA_MODEL_STORE_API_KEY") or os.getenv("NETRIAS_API_KEY")


def validate_required_config() -> None:
    """
    why: Validate all required configuration at startup.

    Call this during Docker build or app initialization to fail fast.
    Raises ConfigurationError if any required config is missing.
    """
    _ = get_data_model_key()
