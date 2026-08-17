"""Temporary, pure CDE suggestions from exact permissible-value overlap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_profile import ColumnProfile
from src.domain.columns import ColumnKey, column_key_from_string

MINIMUM_OVERLAP_PERCENT = 2
MAXIMUM_SUGGESTIONS = 5


@dataclass(frozen=True)
class ValueOverlapCandidate:
    cde_key: str
    overlap_ratio: float
    matched_value_count: int
    source_value_count: int


@dataclass(frozen=True)
class ValueOverlapSuggestions:
    by_column: Mapping[ColumnKey, tuple[ValueOverlapCandidate, ...]]


def suggest_value_overlap_mappings(
    profiles: Mapping[str, ColumnProfile],
    pvs: CdePvCatalog,
) -> ValueOverlapSuggestions:
    """Return evidence only; the caller decides whether to apply a candidate."""
    by_column: dict[ColumnKey, tuple[ValueOverlapCandidate, ...]] = {}
    for raw_column_key, profile in profiles.items():
        source_values = frozenset(value.value for value in profile.distinct_values if value.value != "")
        source_count = len(source_values)
        if source_count == 0:
            continue
        candidates = _candidates(source_values, source_count, pvs)
        if candidates:
            by_column[column_key_from_string(raw_column_key)] = tuple(candidates[:MAXIMUM_SUGGESTIONS])
    return ValueOverlapSuggestions(by_column)


def _candidates(
    source_values: frozenset[str],
    source_count: int,
    pvs: CdePvCatalog,
) -> list[ValueOverlapCandidate]:
    candidates: list[ValueOverlapCandidate] = []
    for cde_key, target_values in pvs.values.items():
        matched_count = len(source_values & target_values)
        if matched_count == 0:
            continue
        # Integer arithmetic makes the 2% boundary exact.
        if matched_count * 100 < source_count * MINIMUM_OVERLAP_PERCENT:
            continue
        candidates.append(
            ValueOverlapCandidate(
                cde_key=cde_key,
                overlap_ratio=matched_count / source_count,
                matched_value_count=matched_count,
                source_value_count=source_count,
            )
        )
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.overlap_ratio,
            -candidate.matched_value_count,
            candidate.cde_key,
        ),
    )


__all__ = [
    "MAXIMUM_SUGGESTIONS",
    "MINIMUM_OVERLAP_PERCENT",
    "ValueOverlapCandidate",
    "ValueOverlapSuggestions",
    "suggest_value_overlap_mappings",
]
