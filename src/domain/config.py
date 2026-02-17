"""
Build-level configuration for data model integration.

Centralized here to allow fail-fast validation at Docker build time.
"""

from __future__ import annotations

import os


class ConfigurationError(Exception):
    pass


_NETRIAS_API_KEY_VAR = "NETRIAS_API_KEY"


def get_netrias_api_key() -> str | None:
    return os.getenv(_NETRIAS_API_KEY_VAR)


def validate_required_config() -> None:
    """Call at startup or Docker build to fail fast on missing config."""
    if not get_netrias_api_key():
        raise ConfigurationError(f"{_NETRIAS_API_KEY_VAR} environment variable is required")
