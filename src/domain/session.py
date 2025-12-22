"""
Define session storage keys and UI labels for browser state management.

Centralize key names used for sessionStorage across stages.
"""

from __future__ import annotations

from enum import Enum


class SessionKey(str, Enum):
    """why: centralize browser sessionStorage key names to avoid duplication."""

    STAGE_THREE_PAYLOAD = "stage3HarmonizePayload"
    STAGE_THREE_JOB = "stage3HarmonizeJob"


class UILabel(str, Enum):
    """why: centralize UI-facing labels to ensure consistency across frontend and backend."""

    NO_MAPPING = "No Mapping"
    SELECT_MAPPING = "Select mapping"
