"""Change classification types for harmonization."""

from __future__ import annotations

from enum import Enum


class RecommendationType(str, Enum):
    """Whether the AI recommendation changed the original value."""

    AI_CHANGED = "ai_changed"
    AI_UNCHANGED = "ai_unchanged"
    NO_RECOMMENDATION = "no_recommendation"
