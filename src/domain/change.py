"""
Define change classification types and confidence thresholds.

Centralize how cell modifications are categorized and scored.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ChangeType(str, Enum):
    """why: classify how a cell value was modified during harmonization."""

    UNCHANGED = "unchanged"
    AI_HARMONIZED = "ai"
    MANUAL_OVERRIDE = "manual"


@dataclass(frozen=True)
class ConfidenceThresholds:
    """why: centralize confidence scoring constants."""

    HIGH: float = 0.9
    LOW: float = 0.3
    MANUAL: float = 0.2


CONFIDENCE = ConfidenceThresholds()
