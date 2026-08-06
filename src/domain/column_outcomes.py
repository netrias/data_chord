"""Current-output facts and per-column harmonization outcome summaries.

This module owns the character-exact meaning of a changed value. Stage-specific
code resolves provider results and active reviewer overrides into
``FinalizedValueOutcome`` values; this module only aggregates those trusted
facts. It deliberately knows nothing about manifests, persistence, HTTP, or
browser response shapes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from src.domain.columns import ColumnKey


class FinalValueSource(str, Enum):
    """The source currently controlling a value in the downloadable output."""

    SOURCE = "source"
    DATA_CHORD = "data_chord"
    REVIEWER = "reviewer"


class FinalValueReviewStatus(str, Enum):
    """Whether current final values were checked and need reviewer attention."""

    CLEAR = "clear"
    NEEDS_ATTENTION = "needs_attention"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class FinalizedValueOutcome:
    """One current final-value result, possibly shared by several source rows."""

    column_key: ColumnKey
    source_column_index: int
    column_label: str
    original_value: str
    final_value: str
    final_value_source: FinalValueSource
    occurrence_count: int
    pv_set_available: bool
    is_pv_conformant: bool

    def __post_init__(self) -> None:
        if self.source_column_index < 0:
            raise ValueError("source_column_index must be non-negative")
        if self.occurrence_count < 1:
            raise ValueError("occurrence_count must be positive")

    @property
    def is_changed(self) -> bool:
        """Case and whitespace are meaningful in harmonized data."""
        return self.original_value != self.final_value

    @property
    def review_status(self) -> FinalValueReviewStatus:
        if not self.pv_set_available:
            return FinalValueReviewStatus.NOT_CHECKED
        if self.is_pv_conformant:
            return FinalValueReviewStatus.CLEAR
        return FinalValueReviewStatus.NEEDS_ATTENTION


@dataclass(frozen=True)
class ColumnOutcome:
    """Current output effects for one stable source column."""

    column_key: ColumnKey
    source_column_index: int
    column_label: str
    total_distinct_values: int
    changed_distinct_values: int
    total_rows: int
    changed_rows: int
    data_chord_changed_rows: int
    reviewer_edited_rows: int
    data_chord_changed_distinct_values: int
    reviewer_changed_distinct_values: int
    non_conformant_distinct_values: int
    review_status: FinalValueReviewStatus


def summarize_column_outcomes(outcomes: list[FinalizedValueOutcome]) -> list[ColumnOutcome]:
    """Aggregate finalized values without changing their original column order."""
    by_column: dict[ColumnKey, list[FinalizedValueOutcome]] = defaultdict(list)
    for outcome in outcomes:
        by_column[outcome.column_key].append(outcome)

    summaries = [_summarize_column(column_outcomes) for column_outcomes in by_column.values()]
    return sorted(summaries, key=lambda summary: summary.source_column_index)


def _summarize_column(outcomes: list[FinalizedValueOutcome]) -> ColumnOutcome:
    first = outcomes[0]
    original_values: set[str] = set()
    changed_originals: set[str] = set()
    reviewer_changed_originals: set[str] = set()
    non_conformant_originals: set[str] = set()
    total_rows = 0
    changed_rows = 0
    data_chord_changed_rows = 0
    reviewer_edited_rows = 0
    all_pv_sets_available = True

    for outcome in outcomes:
        if (
            outcome.source_column_index != first.source_column_index
            or outcome.column_label != first.column_label
        ):
            raise ValueError(f"Inconsistent identity for source column {first.column_key}")

        original_values.add(outcome.original_value)
        total_rows += outcome.occurrence_count
        all_pv_sets_available = all_pv_sets_available and outcome.pv_set_available

        if outcome.final_value_source is FinalValueSource.REVIEWER:
            reviewer_edited_rows += outcome.occurrence_count
        if outcome.review_status is FinalValueReviewStatus.NEEDS_ATTENTION:
            non_conformant_originals.add(outcome.original_value)
        if not outcome.is_changed:
            continue

        changed_originals.add(outcome.original_value)
        changed_rows += outcome.occurrence_count
        if outcome.final_value_source is FinalValueSource.DATA_CHORD:
            data_chord_changed_rows += outcome.occurrence_count
        elif outcome.final_value_source is FinalValueSource.REVIEWER:
            reviewer_changed_originals.add(outcome.original_value)

    if non_conformant_originals:
        review_status = FinalValueReviewStatus.NEEDS_ATTENTION
    elif all_pv_sets_available:
        review_status = FinalValueReviewStatus.CLEAR
    else:
        review_status = FinalValueReviewStatus.NOT_CHECKED

    return ColumnOutcome(
        column_key=first.column_key,
        source_column_index=first.source_column_index,
        column_label=first.column_label,
        total_distinct_values=len(original_values),
        changed_distinct_values=len(changed_originals),
        total_rows=total_rows,
        changed_rows=changed_rows,
        data_chord_changed_rows=data_chord_changed_rows,
        reviewer_edited_rows=reviewer_edited_rows,
        data_chord_changed_distinct_values=len(changed_originals - reviewer_changed_originals),
        reviewer_changed_distinct_values=len(reviewer_changed_originals),
        non_conformant_distinct_values=len(non_conformant_originals),
        review_status=review_status,
    )


__all__ = [
    "ColumnOutcome",
    "FinalizedValueOutcome",
    "FinalValueReviewStatus",
    "FinalValueSource",
    "summarize_column_outcomes",
]
