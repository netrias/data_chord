"""Change classification types and confidence thresholds for harmonization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ChangeType(str, Enum):
    """How a reviewed value was produced."""

    UNCHANGED = "unchanged"
    AI_HARMONIZED = "ai"
    MANUAL_OVERRIDE = "manual"


class RecommendationType(str, Enum):
    """Whether the AI recommendation changed the original value."""

    AI_CHANGED = "ai_changed"
    AI_UNCHANGED = "ai_unchanged"
    NO_RECOMMENDATION = "no_recommendation"


@dataclass(frozen=True)
class ConfidenceThresholds:
    """Shared confidence defaults used when classifying review changes."""

    HIGH: float = 0.9
    LOW: float = 0.3
    MANUAL: float = 0.2


CONFIDENCE = ConfidenceThresholds()
