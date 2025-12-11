"""
Define session storage keys for browser state management.

Centralize key names used for sessionStorage across stages.
"""

from __future__ import annotations

from enum import Enum


class SessionKey(str, Enum):
    """why: centralize browser sessionStorage key names to avoid duplication."""

    STAGE_THREE_PAYLOAD = "stage3HarmonizePayload"
    STAGE_THREE_JOB = "stage3HarmonizeJob"
