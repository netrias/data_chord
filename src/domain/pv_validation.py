"""
Pure functions for validating values against permissible value sets.

Validation logic is kept pure (no I/O) to enable testing without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AdjustmentSource(str, Enum):
    TOP_SUGGESTIONS = "top_suggestions"
    ORIGINAL = "original"
    PV_OVERRIDE = "pv_override"


@dataclass(frozen=True)
class PVValidationResult:
    is_conformant: bool
    original_value: str
    attempted_value: str  # What we tried to validate
    adjusted_value: str | None  # If non-conformant, what we adjusted to
    adjustment_source: AdjustmentSource | None


def validate_against_pvs(value: str, pv_set: frozenset[str]) -> bool:
    """Whitespace-sensitive comparison (per domain rules in CLAUDE.md)."""
    return value in pv_set


def find_conformant_suggestion(
    top_suggestions: list[str],
    pv_set: frozenset[str],
) -> str | None:
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
    """Never convert a valid original value to something else.

    If the user's original value is already in the PV set, we keep it regardless
    of what the AI suggested. This prevents "correcting" valid data.
    """
    if validate_against_pvs(original_value, pv_set):
        if original_value != top_harmonization:
            return PVValidationResult(
                is_conformant=True,
                original_value=original_value,
                attempted_value=top_harmonization,
                adjusted_value=original_value,
                adjustment_source=AdjustmentSource.PV_OVERRIDE,
            )
        return PVValidationResult(
            is_conformant=True,
            original_value=original_value,
            attempted_value=top_harmonization,
            adjusted_value=None,
            adjustment_source=None,
        )

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
    """Assume conformant when PVs unavailable (graceful degradation).

    None/empty values are conformant since they represent missing data, not invalid data.
    """
    if pv_set is None or not pv_set:
        return True
    if value is None or value == "":
        return True
    return value in pv_set
