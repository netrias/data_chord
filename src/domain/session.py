"""Centralize session storage keys and UI labels for browser state management."""

from __future__ import annotations

from enum import Enum


class SessionKey(str, Enum):
    STAGE_THREE_PAYLOAD = "stage3HarmonizePayload"
    STAGE_THREE_JOB = "stage3HarmonizeJob"


class UILabel(str, Enum):
    NO_MAPPING = "No Mapping"
    SELECT_MAPPING = "Select mapping"


