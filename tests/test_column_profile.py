"""Tests for ColumnProfile and build_column_profile.

The profile is the canonical representation of "what's in a column": distinct
values sorted by frequency, null count, and totals. Drives the Stage 2
takeover's left pane.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from src.domain.column_profile import (
    ColumnProfile,
    DistinctValue,
    build_column_profile,
)

# ---------------------------------------------------------------------------
# Test: build_column_profile counts and sorts by frequency
# ---------------------------------------------------------------------------


def test_build_column_profile_counts_and_sorts_by_frequency() -> None:
    """
    Given: a column with values that repeat at different rates
    When: build_column_profile is called
    Then: distinct_values are sorted by count descending; counts match input
    """
    # Given
    values = ["a", "b", "a", "c", "a", "b"]
    # negative assertion: profile not built yet
    profile: ColumnProfile | None = None
    assert profile is None

    # When
    profile = build_column_profile("col", values)

    # Then
    assert profile.column_key == "col"
    assert profile.total_rows == 6
    assert profile.distinct_values == (
        DistinctValue(value="a", count=3),
        DistinctValue(value="b", count=2),
        DistinctValue(value="c", count=1),
    )


# ---------------------------------------------------------------------------
# Test: nulls are counted, not included in distinct_values
# ---------------------------------------------------------------------------


def test_build_column_profile_treats_none_and_empty_string_as_null() -> None:
    """
    Given: a column with None and empty-string values mixed with real values
    When: build_column_profile is called
    Then: null_count covers both None and "", and they are not in distinct_values
    """
    # Given
    values = ["x", None, "y", "", "x"]

    # When
    profile = build_column_profile("col", values)

    # Then
    assert profile.null_count == 2  # one None, one ""
    assert {dv.value for dv in profile.distinct_values} == {"x", "y"}


# ---------------------------------------------------------------------------
# Test: derived properties (total_distinct, null_pct, is_all_unique)
# ---------------------------------------------------------------------------


def test_column_profile_derived_properties() -> None:
    """
    Given: a column with 4 rows, 3 distinct values, 1 null
    When: derived properties are read
    Then: total_distinct=3, null_pct=25.0, is_all_unique=False
    """
    # Given
    values = ["a", "b", "c", None]

    # When
    profile = build_column_profile("col", values)

    # Then
    assert profile.total_distinct == 3
    assert profile.null_pct == 25.0
    assert profile.is_all_unique is False


def test_column_profile_is_all_unique_when_distinct_equals_total_rows() -> None:
    """
    Given: a column where every value is unique (e.g. a patient_id column)
    When: is_all_unique is read
    Then: True
    """
    # Given
    values = ["pt_001", "pt_002", "pt_003"]

    # When
    profile = build_column_profile("patient_id", values)

    # Then
    assert profile.is_all_unique is True


def test_column_profile_null_pct_zero_when_no_nulls() -> None:
    """
    Given: a fully populated column
    When: null_pct is read
    Then: 0.0 (no rounding artifacts)
    """
    # Given
    values = ["x", "x", "x"]

    # When
    profile = build_column_profile("col", values)

    # Then
    assert profile.null_pct == 0.0


# ---------------------------------------------------------------------------
# Test: empty column produces an empty profile
# ---------------------------------------------------------------------------


def test_build_column_profile_handles_empty_column() -> None:
    """
    Given: a column with zero rows
    When: build_column_profile is called
    Then: profile has total_rows=0, no distinct values, null_pct=0.0
    """
    # Given / When
    profile = build_column_profile("col", [])

    # Then
    assert profile.total_rows == 0
    assert profile.distinct_values == ()
    assert profile.null_count == 0
    assert profile.null_pct == 0.0


# ---------------------------------------------------------------------------
# Property: total_rows always equals sum(counts) + null_count
# ---------------------------------------------------------------------------


@given(
    values=st.lists(
        st.one_of(
            st.text(min_size=0, max_size=20),
            st.none(),
        ),
        max_size=200,
    ),
)
def test_total_rows_equals_distinct_count_sum_plus_nulls(values: list[str | None]) -> None:
    """
    Given: any list of values (including None and "")
    When: a profile is built
    Then: sum(distinct counts) + null_count == total_rows (the conservation law)
    """
    profile = build_column_profile("col", values)
    counts_sum = sum(dv.count for dv in profile.distinct_values)
    assert counts_sum + profile.null_count == profile.total_rows


# ---------------------------------------------------------------------------
# Property: distinct_values is sorted by count descending
# ---------------------------------------------------------------------------


@given(
    values=st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=100),
)
def test_distinct_values_sorted_by_count_desc(values: list[str]) -> None:
    """
    Given: any non-empty list of non-null values
    When: a profile is built
    Then: distinct_values are sorted with highest count first
    """
    profile = build_column_profile("col", values)
    counts = [dv.count for dv in profile.distinct_values]
    assert counts == sorted(counts, reverse=True)
