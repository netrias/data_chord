"""Tests for compute_match_counts — type-aware conformance counting."""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from src.domain.cde import CDEInfo, CdeType
from src.domain.match_counts import (
    column_value_overlap_ratio,
    compute_column_overlap_by_cde,
    compute_match_counts,
)

# ---------------------------------------------------------------------------
# Test: PV-typed CDE counts set intersection
# ---------------------------------------------------------------------------


def test_pv_type_counts_set_intersection() -> None:
    """
    Given: a PV-typed CDE with PVs and a column whose distinct values overlap
    When: compute_match_counts runs
    Then: count == size of the intersection
    """
    # Given
    cdes = [_make_cde("diagnosis", CdeType.PV)]
    pv_sets = {"diagnosis": frozenset({"Lung Cancer", "Breast Cancer", "Glioma"})}
    distinct = frozenset({"Lung Cancer", "Breast Cancer", "lung cancer"})  # case-sensitive
    # negative assertion: nothing computed yet
    counts: dict[str, int] = {}
    assert counts == {}

    # When
    counts = compute_match_counts(distinct, cdes, pv_sets)

    # Then: only exact-matching values are conformant (per project's whitespace rules)
    assert counts == {"diagnosis": 2}


# ---------------------------------------------------------------------------
# Test: NUMERIC type counts values that parse as float
# ---------------------------------------------------------------------------


def test_numeric_type_counts_parseable_values() -> None:
    """
    Given: a NUMERIC-typed CDE and distinct values that mix numeric and non-numeric
    When: compute_match_counts runs
    Then: count == number of values that parse as float
    """
    # Given
    cdes = [_make_cde("age", CdeType.NUMERIC)]
    distinct = frozenset({"57", "62", "twenty", "29.5", "n/a"})

    # When
    counts = compute_match_counts(distinct, cdes, pv_sets={})

    # Then: "57", "62", "29.5" are numeric
    assert counts == {"age": 3}


# ---------------------------------------------------------------------------
# Test: PASSTHROUGH type counts every distinct value
# ---------------------------------------------------------------------------


def test_passthrough_type_counts_all_distinct_values() -> None:
    """
    Given: a PASSTHROUGH-typed CDE and arbitrary distinct values
    When: compute_match_counts runs
    Then: count == number of distinct values (everything passes)
    """
    # Given
    cdes = [_make_cde("free_notes", CdeType.PASSTHROUGH)]
    distinct = frozenset({"alpha", "beta", "gamma"})

    # When
    counts = compute_match_counts(distinct, cdes, pv_sets={})

    # Then
    assert counts == {"free_notes": 3}


# ---------------------------------------------------------------------------
# Test: zero-count entries are dropped (sparse output)
# ---------------------------------------------------------------------------


def test_zero_count_entries_omitted_from_output() -> None:
    """
    Given: a PV-typed CDE with no overlap and a NUMERIC-typed CDE with non-numeric data
    When: compute_match_counts runs
    Then: neither key appears in the output (sparse map)
    """
    # Given
    cdes = [
        _make_cde("diagnosis", CdeType.PV),
        _make_cde("age", CdeType.NUMERIC),
    ]
    pv_sets = {"diagnosis": frozenset({"X", "Y"})}
    distinct = frozenset({"foo", "bar"})

    # When
    counts = compute_match_counts(distinct, cdes, pv_sets)

    # Then
    assert counts == {}


def test_column_value_overlap_ratio_for_pv_cde() -> None:
    """
    Given: a PV-typed CDE and a non-empty column distinct set
    When: the value overlap ratio is computed
    Then: the ratio is exact set membership over the distinct values
    """
    # Given
    distinct = frozenset({"Lung", "Breast", "Unknown", "Glioma"})
    pv_set = frozenset({"Lung", "Breast", "Glioma", "Other"})
    ratio: float | None = None
    assert ratio is None

    # When
    ratio = column_value_overlap_ratio(distinct, CdeType.PV, pv_set)

    # Then
    assert ratio == 0.75


def test_column_value_overlap_ratio_distinguishes_zero_from_undefined() -> None:
    """
    Given: a PV-typed CDE with no matching values
    When: the column has distinct values
    Then: the overlap is 0.0, not None
    """
    # Given
    distinct = frozenset({"a", "b"})
    pv_set = frozenset({"x", "y"})
    assert distinct.isdisjoint(pv_set)

    # When
    ratio = column_value_overlap_ratio(distinct, CdeType.PV, pv_set)

    # Then
    assert ratio == 0.0


def test_column_value_overlap_ratio_returns_none_when_undefined() -> None:
    """
    Given: inputs where value overlap has no domain meaning
    When: the ratio is computed
    Then: None is returned for each undefined case
    """
    # Given
    distinct = frozenset({"1", "2"})
    undefined_inputs = [
        (frozenset(), CdeType.PV, frozenset({"1"})),
        (distinct, CdeType.PV, None),
        (distinct, CdeType.NUMERIC, None),
        (distinct, CdeType.PASSTHROUGH, None),
    ]
    assert all(args[0] == frozenset() or args[1] != CdeType.PV or args[2] is None for args in undefined_inputs)

    # When
    ratios = [column_value_overlap_ratio(*args) for args in undefined_inputs]

    # Then
    assert ratios == [None, None, None, None]


def test_compute_column_overlap_by_cde_includes_zero_pv_and_omits_undefined() -> None:
    """
    Given: a mixed CDE catalog and a column with distinct values
    When: per-CDE overlap ratios are computed
    Then: PV CDEs with fetched PVs appear, including zero overlap, and rename-only CDEs do not
    """
    # Given
    cdes = [
        _make_cde("dx", CdeType.PV),
        _make_cde("zero", CdeType.PV),
        _make_cde("missing", CdeType.PV),
        _make_cde("age", CdeType.NUMERIC),
        _make_cde("notes", CdeType.PASSTHROUGH),
    ]
    distinct = frozenset({"Lung", "Unknown"})
    pv_sets = {
        "dx": frozenset({"Lung", "Breast"}),
        "zero": frozenset({"Glioma"}),
    }
    overlaps: dict[str, float] = {}
    assert overlaps == {}

    # When
    overlaps = compute_column_overlap_by_cde(distinct, cdes, pv_sets)

    # Then
    assert overlaps == {"dx": 0.5, "zero": 0.0}


def test_compute_column_overlap_by_cde_returns_empty_for_empty_distinct_values() -> None:
    """
    Given: a PV CDE with fetched PVs and no distinct column values
    When: per-CDE overlap ratios are computed
    Then: the sparse result is empty because the ratio is undefined
    """
    # Given
    cdes = [_make_cde("dx", CdeType.PV)]
    distinct = frozenset()
    assert len(distinct) == 0

    # When
    overlaps = compute_column_overlap_by_cde(distinct, cdes, {"dx": frozenset({"Lung"})})

    # Then
    assert overlaps == {}


# ---------------------------------------------------------------------------
# Property: output count is never greater than |distinct_values|
# ---------------------------------------------------------------------------


@given(
    distinct=st.sets(st.text(min_size=0, max_size=10), max_size=50),
    pv=st.sets(st.text(min_size=0, max_size=10), max_size=50),
)
def test_pv_count_never_exceeds_distinct(distinct: set[str], pv: set[str]) -> None:
    """
    Given: any distinct value set and PV set
    When: compute_match_counts runs against a single PV-typed CDE
    Then: result count ≤ |distinct_values|
    """
    cdes = [_make_cde("c", CdeType.PV)]
    counts = compute_match_counts(frozenset(distinct), cdes, {"c": frozenset(pv)})
    assert counts.get("c", 0) <= len(distinct)


# ---------------------------------------------------------------------------
# Property: PASSTHROUGH always returns |distinct_values| (or omits when empty)
# ---------------------------------------------------------------------------


@given(distinct=st.sets(st.text(min_size=1, max_size=10), max_size=30))
def test_passthrough_count_equals_distinct_count(distinct: set[str]) -> None:
    """
    Given: any distinct value set (non-empty)
    When: compute_match_counts runs against a PASSTHROUGH CDE
    Then: count == |distinct_values| (everything passes through)
    """
    cdes = [_make_cde("pass", CdeType.PASSTHROUGH)]
    counts = compute_match_counts(frozenset(distinct), cdes, pv_sets={})
    if distinct:
        assert counts["pass"] == len(distinct)
    else:
        assert "pass" not in counts


@given(
    distinct=st.sets(st.text(min_size=0, max_size=10), max_size=40),
    pv=st.sets(st.text(min_size=0, max_size=10), max_size=40),
    extra=st.sets(st.text(min_size=0, max_size=10), max_size=40),
)
def test_column_value_overlap_ratio_bounds_and_monotonicity(
    distinct: set[str],
    pv: set[str],
    extra: set[str],
) -> None:
    """
    Given: any non-empty distinct value set and PV set
    When: the PV set grows
    Then: the overlap ratio stays in bounds and never decreases
    """
    assume(len(distinct) > 0)
    distinct_set = frozenset(distinct)
    pv_set = frozenset(pv)

    ratio = column_value_overlap_ratio(distinct_set, CdeType.PV, pv_set)
    larger_ratio = column_value_overlap_ratio(distinct_set, CdeType.PV, pv_set | frozenset(extra))

    assert ratio is not None
    assert larger_ratio is not None
    assert 0.0 <= ratio <= 1.0
    assert ratio <= larger_ratio


def _make_cde(key: str, cde_type: CdeType) -> CDEInfo:
    return CDEInfo(
        cde_id=hash(key) & 0xFFFF,
        cde_key=key,
        description=None,
        version_label="1",
        cde_type=cde_type,
    )
