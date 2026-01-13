"""
Build-level configuration for data model integration.

Centralized here to allow fail-fast validation at Docker build time.
"""

from __future__ import annotations

import os


class ConfigurationError(Exception):
    pass


_DATA_MODEL_KEY_VAR = "DATA_MODEL_KEY"


def get_data_model_key() -> str:
    key = os.getenv(_DATA_MODEL_KEY_VAR)
    if not key:
        raise ConfigurationError(f"{_DATA_MODEL_KEY_VAR} environment variable is required")
    return key


def get_data_model_store_api_key() -> str | None:
    """Falls back to NETRIAS_API_KEY if DATA_MODEL_STORE_API_KEY not set."""
    return os.getenv("DATA_MODEL_STORE_API_KEY") or os.getenv("NETRIAS_API_KEY")


def validate_required_config() -> None:
    """Call at startup or Docker build to fail fast on missing config."""
    _ = get_data_model_key()
