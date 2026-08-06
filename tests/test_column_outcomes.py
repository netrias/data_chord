"""Behavioral proof for the shared current-output summary rules."""

from __future__ import annotations

import pytest

from src.domain.column_outcomes import (
    FinalizedValueOutcome,
    FinalValueReviewStatus,
    FinalValueSource,
    summarize_column_outcomes,
)
from src.domain.columns import column_key_for_index


def _outcome(
    *,
    column_index: int = 0,
    label: str = "diagnosis",
    original: str = "Lung Cancer",
    final: str = "Lung Cancer",
    source: FinalValueSource = FinalValueSource.SOURCE,
    occurrences: int = 1,
    pv_available: bool = True,
    conformant: bool = True,
) -> FinalizedValueOutcome:
    return FinalizedValueOutcome(
        column_key=column_key_for_index(column_index),
        source_column_index=column_index,
        column_label=label,
        original_value=original,
        final_value=final,
        final_value_source=source,
        occurrence_count=occurrences,
        pv_set_available=pv_available,
        is_pv_conformant=conformant,
    )


@pytest.mark.parametrize("final", ["lung cancer", "Lung Cancer ", " Lung Cancer"])
def test_case_and_whitespace_differences_are_changes(final: str) -> None:
    summary = summarize_column_outcomes([
        _outcome(final=final, source=FinalValueSource.DATA_CHORD, occurrences=3),
    ])[0]

    assert summary.changed_distinct_values == 1
    assert summary.changed_rows == 3
    assert summary.data_chord_changed_rows == 3


def test_repeated_value_weights_rows_without_inflating_distinct_values() -> None:
    summary = summarize_column_outcomes([
        _outcome(original="A", final="B", source=FinalValueSource.DATA_CHORD, occurrences=5),
        _outcome(original="C", final="C", occurrences=2),
    ])[0]

    assert summary.total_distinct_values == 2
    assert summary.changed_distinct_values == 1
    assert summary.total_rows == 7
    assert summary.changed_rows == 5


def test_change_state_and_reviewer_provenance_are_independent() -> None:
    summary = summarize_column_outcomes([
        _outcome(
            original="A",
            final="A",
            source=FinalValueSource.REVIEWER,
            occurrences=2,
        ),
        _outcome(
            original="B",
            final="C",
            source=FinalValueSource.REVIEWER,
            occurrences=1,
        ),
    ])[0]

    assert summary.changed_distinct_values == 1
    assert summary.changed_rows == 1
    assert summary.reviewer_edited_rows == 3
    assert summary.reviewer_changed_distinct_values == 1
    assert summary.data_chord_changed_distinct_values == 0


def test_one_distinct_value_is_partitioned_to_reviewer_when_any_changed_occurrence_is_reviewed() -> None:
    summary = summarize_column_outcomes([
        _outcome(
            original="A",
            final="B",
            source=FinalValueSource.DATA_CHORD,
            occurrences=2,
        ),
        _outcome(
            original="A",
            final="C",
            source=FinalValueSource.REVIEWER,
            occurrences=1,
        ),
    ])[0]

    assert summary.changed_distinct_values == 1
    assert summary.reviewer_changed_distinct_values == 1
    assert summary.data_chord_changed_distinct_values == 0


@pytest.mark.parametrize(
    ("pv_available", "conformant", "expected_status"),
    [
        (False, True, FinalValueReviewStatus.NOT_CHECKED),
        (True, True, FinalValueReviewStatus.CLEAR),
        (True, False, FinalValueReviewStatus.NEEDS_ATTENTION),
    ],
)
def test_final_value_review_status_is_explicit(
    pv_available: bool,
    conformant: bool,
    expected_status: FinalValueReviewStatus,
) -> None:
    summary = summarize_column_outcomes([
        _outcome(pv_available=pv_available, conformant=conformant),
    ])[0]

    assert summary.review_status is expected_status
    assert summary.non_conformant_distinct_values == int(
        expected_status is FinalValueReviewStatus.NEEDS_ATTENTION
    )


def test_columns_are_sorted_by_source_index_not_activity_or_label() -> None:
    summaries = summarize_column_outcomes([
        _outcome(column_index=2, label="alpha", original="A", final="B"),
        _outcome(column_index=0, label="zeta", original="Z", final="Z"),
        _outcome(column_index=1, label="middle", original="M", final="M"),
    ])

    assert [summary.source_column_index for summary in summaries] == [0, 1, 2]
    assert [summary.column_label for summary in summaries] == ["zeta", "middle", "alpha"]


def test_one_column_key_cannot_mix_source_identity() -> None:
    with pytest.raises(ValueError, match="Inconsistent identity"):
        summarize_column_outcomes([
            _outcome(column_index=0, label="diagnosis"),
            FinalizedValueOutcome(
                column_key=column_key_for_index(0),
                source_column_index=1,
                column_label="other",
                original_value="A",
                final_value="A",
                final_value_source=FinalValueSource.SOURCE,
                occurrence_count=1,
                pv_set_available=True,
                is_pv_conformant=True,
            ),
        ])


def test_invalid_occurrence_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="occurrence_count"):
        _outcome(occurrences=0)
