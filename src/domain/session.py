"""Centralize session storage keys and UI labels for browser state management."""

from __future__ import annotations

from enum import Enum

from src.domain.cde import NO_MAPPING_SENTINEL


class SessionKey(str, Enum):
    STAGE_THREE_PAYLOAD = "stage3HarmonizePayload"
    STAGE_THREE_JOB = "stage3HarmonizeJob"


class UILabel(str, Enum):
    NO_MAPPING = NO_MAPPING_SENTINEL


def format_column_label(column_name: str) -> str:
    if not column_name:
        return "Unknown"
    return column_name.replace("_", " ").title()
