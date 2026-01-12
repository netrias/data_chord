"""
Pure functions for validating values against permissible value sets.

why: Separate validation logic from I/O (CQS principle).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AdjustmentSource(str, Enum):
    """why: Track how a PV adjustment was determined."""

    TOP_SUGGESTIONS = "top_suggestions"
    ORIGINAL = "original"


@dataclass(frozen=True)
class PVValidationResult:
    """why: Result of validating a value against PV set."""

    is_conformant: bool
    original_value: str
    attempted_value: str  # What we tried to validate
    adjusted_value: str | None  # If non-conformant, what we adjusted to
    adjustment_source: AdjustmentSource | None


def validate_against_pvs(value: str, pv_set: frozenset[str]) -> bool:
    """
    Check if value is in the PV set.

    Exact match, whitespace-sensitive (per domain rules).
    """
    return value in pv_set


def find_conformant_suggestion(
    top_suggestions: list[str],
    pv_set: frozenset[str],
) -> str | None:
    """why: Return first suggestion that is in PV set, or None."""
    for suggestion in top_suggestions:
        if suggestion in pv_set:
            return suggestion
    return None


def compute_pv_adjustment(
    original_value: str,
    top_harmonization: str,
    top_suggestions: list[str],
    pv_set: frozenset[str],
) -> PVValidationResult:
    """
    Determine if value needs PV adjustment and what the adjustment should be.

    Logic:
    1. If top_harmonization is in PV set -> conformant, no adjustment
    2. Else, find first suggestion in PV set -> adjust to that
    3. Else, no valid suggestion -> keep AI suggestion, mark as non-conformant
    """
    if validate_against_pvs(top_harmonization, pv_set):
        return PVValidationResult(
            is_conformant=True,
            original_value=original_value,
            attempted_value=top_harmonization,
            adjusted_value=None,
            adjustment_source=None,
        )

    alt = find_conformant_suggestion(top_suggestions, pv_set)
    if alt is not None:
        return PVValidationResult(
            is_conformant=True,
            original_value=original_value,
            attempted_value=top_harmonization,
            adjusted_value=alt,
            adjustment_source=AdjustmentSource.TOP_SUGGESTIONS,
        )

    # No conformant option found -> keep AI suggestion, mark as non-conformant
    # Don't auto-adjust; let user see the AI suggestion and decide via override
    return PVValidationResult(
        is_conformant=False,
        original_value=original_value,
        attempted_value=top_harmonization,
        adjusted_value=None,
        adjustment_source=None,
    )


def check_value_conformance(
    value: str | None,
    pv_set: frozenset[str] | None,
) -> bool:
    """
    Check if a value conforms to its PV set.

    Returns True if:
    - pv_set is None (no PVs available, assume conformant)
    - pv_set is empty (no PVs defined, assume conformant)
    - value is None or empty (nothing to validate)
    - value is in pv_set

    Returns False otherwise.
    """
    if pv_set is None or not pv_set:
        return True
    if not value:
        return True
    return value in pv_set
