"""
Property-based tests for domain logic using Hypothesis.

Tests invariants across randomly generated inputs rather than specific examples.
Complements existing unit tests by exploring edge cases automatically.
"""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.domain.cde import (
    ColumnMappingSet,
    normalize_cde_key,
)
from src.domain.demo_bypass import DEMO_CDE_REGISTRY
from src.domain.harmonize import _normalize_manifest
from src.domain.manifest.models import is_value_changed
from src.domain.pv_validation import (
    AdjustmentSource,
    check_value_conformance,
    compute_pv_adjustment,
    find_conformant_suggestion,
    validate_against_pvs,
)

# =============================================================================
# Value Change Detection Properties
# =============================================================================


@given(st.text())
def test_value_unchanged_with_self(value: str) -> None:
    """A value compared to itself is never changed."""
    assert is_value_changed(value, value) is False


@given(st.text(min_size=1).filter(lambda s: s.strip()))
def test_value_changed_when_different(original: str) -> None:
    """Different non-empty values are detected as changed."""
    harmonized = original + "_modified"
    assert is_value_changed(original, harmonized) is True


@given(st.text())
def test_empty_harmonized_is_not_changed(original: str) -> None:
    """Empty harmonized value means no change (no recommendation made)."""
    assert is_value_changed(original, "") is False
    assert is_value_changed(original, None) is False


@given(st.text().filter(lambda s: not s.strip()))
def test_whitespace_only_harmonized_is_not_changed(whitespace: str) -> None:
    """Whitespace-only harmonized value counts as empty."""
    assert is_value_changed("anything", whitespace) is False


@given(
    st.text(min_size=1).filter(lambda s: s.strip()),
    st.sampled_from([" ", "  ", "\t", "\n", " \t\n "]),
)
def test_whitespace_differences_are_significant(base: str, ws: str) -> None:
    """Whitespace variations are semantically significant per domain rules."""
    original = base
    with_leading = ws + base
    with_trailing = base + ws

    # These are different values in our domain
    if original != with_leading:
        assert is_value_changed(original, with_leading) is True
    if original != with_trailing:
        assert is_value_changed(original, with_trailing) is True


# =============================================================================
# PV Validation Properties
# =============================================================================


@given(st.text(), st.lists(st.text(), min_size=1))
def test_validate_against_pvs_is_membership(value: str, pv_list: list[str]) -> None:
    """Validation is strict set membership."""
    pv_set = frozenset(pv_list)
    result = validate_against_pvs(value, pv_set)
    assert result == (value in pv_set)


@given(st.lists(st.text(), min_size=1))
def test_validate_member_always_passes(pv_list: list[str]) -> None:
    """A value that is in the PV set always validates."""
    pv_set = frozenset(pv_list)
    member = pv_list[0]
    assert validate_against_pvs(member, pv_set) is True


@given(st.text(), st.lists(st.text(), min_size=1))
def test_check_conformance_matches_membership(value: str, pv_list: list[str]) -> None:
    """check_value_conformance agrees with set membership for non-empty values."""
    pv_set = frozenset(pv_list)
    assume(value != "" and value is not None)
    result = check_value_conformance(value, pv_set)
    assert result == (value in pv_set)


@given(st.text())
def test_conformance_with_empty_pvs_is_always_true(value: str) -> None:
    """Graceful degradation: empty/None PV sets pass everything."""
    assert check_value_conformance(value, None) is True
    assert check_value_conformance(value, frozenset()) is True


@given(st.lists(st.text(), min_size=1))
def test_conformance_empty_value_is_always_true(pv_list: list[str]) -> None:
    """Empty/None values are conformant (missing data, not invalid)."""
    pv_set = frozenset(pv_list)
    assert check_value_conformance("", pv_set) is True
    assert check_value_conformance(None, pv_set) is True


# =============================================================================
# Find Conformant Suggestion Properties
# =============================================================================


@given(st.lists(st.text(), min_size=1), st.lists(st.text(), min_size=1))
def test_find_conformant_returns_first_match(suggestions: list[str], pvs: list[str]) -> None:
    """Returns first suggestion that appears in PV set, or None."""
    pv_set = frozenset(pvs)
    result = find_conformant_suggestion(suggestions, pv_set)

    if result is not None:
        # Result must be in both suggestions and pvs
        assert result in suggestions
        assert result in pv_set
        # And it must be the first such match
        for s in suggestions:
            if s in pv_set:
                assert result == s
                break
    else:
        # No suggestion is in the pv_set
        for s in suggestions:
            assert s not in pv_set


@given(st.lists(st.text(), min_size=1))
def test_find_conformant_with_matching_first(pv_list: list[str]) -> None:
    """When first suggestion is conformant, it is returned."""
    pv_set = frozenset(pv_list)
    first_pv = pv_list[0]
    suggestions = [first_pv, "other", "values"]
    assert find_conformant_suggestion(suggestions, pv_set) == first_pv


# =============================================================================
# Compute PV Adjustment Properties
# =============================================================================


@given(
    st.text(),
    st.lists(st.text(), min_size=1),
)
def test_compute_adjustment_conformant_when_top_in_pvs(original: str, pv_list: list[str]) -> None:
    """If top_harmonization is in PV set, result is conformant with no adjustment.

    Note: Excludes cases where original is also in PV set (but differs from top),
    because those trigger PV_OVERRIDE per ADR 004.
    """
    pv_set = frozenset(pv_list)
    top = pv_list[0]  # Use a known member
    assume(original not in pv_set or original == top)

    result = compute_pv_adjustment(original, top, [], pv_set)

    assert result.is_conformant is True
    assert result.adjusted_value is None
    assert result.adjustment_source is None
    assert result.attempted_value == top


@given(
    st.text(),
    st.text(),
    st.lists(st.text(), min_size=1),
)
def test_compute_adjustment_falls_back_to_suggestions(
    original: str, non_conformant_top: str, suggestion_pvs: list[str]
) -> None:
    """If top is non-conformant but a suggestion is, adjustment is made.

    Note: Excludes cases where original is in PV set, because those trigger
    PV_OVERRIDE per ADR 004 (original-first validation).
    """
    pv_set = frozenset(suggestion_pvs)
    assume(non_conformant_top not in pv_set)
    assume(original not in pv_set)
    suggestions = [non_conformant_top, suggestion_pvs[0]]  # Second one is conformant

    result = compute_pv_adjustment(original, non_conformant_top, suggestions, pv_set)

    assert result.is_conformant is True
    assert result.adjusted_value == suggestion_pvs[0]
    assert result.adjustment_source == AdjustmentSource.TOP_SUGGESTIONS


@given(st.text(), st.text(), st.lists(st.text(), max_size=3))
def test_compute_adjustment_non_conformant_when_no_match(
    original: str, top: str, suggestions: list[str]
) -> None:
    """If nothing matches PV set, result is non-conformant."""
    # Create a PV set with values that won't match
    pv_set = frozenset(["__definitely_not_matching_1__", "__definitely_not_matching_2__"])
    assume(top not in pv_set)
    assume(all(s not in pv_set for s in suggestions))

    result = compute_pv_adjustment(original, top, suggestions, pv_set)

    assert result.is_conformant is False
    assert result.adjusted_value is None
    assert result.original_value == original
    assert result.attempted_value == top


# =============================================================================
# CDE Key Normalization Properties
# =============================================================================


@given(st.text(min_size=1, max_size=100).filter(lambda s: s.strip()))
def test_normalize_cde_key_preserves_non_whitespace(text: str) -> None:
    """Non-empty, non-whitespace strings are returned stripped."""
    result = normalize_cde_key(text)
    assert result is not None
    assert result == text.strip()


@given(st.text(min_size=1, max_size=100).filter(lambda s: s.strip()))
def test_normalize_cde_key_is_idempotent(text: str) -> None:
    """Normalizing twice produces the same result."""
    first = normalize_cde_key(text)
    assert first is not None
    second = normalize_cde_key(first)
    assert second == first


@given(st.sampled_from([None, "", "   ", "\t\n"]))
def test_normalize_cde_key_empty_returns_none(empty: str | None) -> None:
    """Empty or whitespace-only input returns None."""
    assert normalize_cde_key(empty) is None


# =============================================================================
# ColumnMappingSet Roundtrip Properties
# =============================================================================


@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    values=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    min_size=1,
    max_size=10,
))
def test_column_mapping_roundtrip_preserves_columns(overrides: dict[str, str]) -> None:
    """from_dict preserves column names through to_dict."""
    mapping_set = ColumnMappingSet.from_dict(overrides)
    result = mapping_set.to_dict()

    # All original columns should be present
    assert set(result.keys()) == set(overrides.keys())


@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    values=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    min_size=1,
    max_size=10,
))
def test_column_mapping_values_are_cleaned(overrides: dict[str, str]) -> None:
    """All values are cleaned (stripped) strings."""
    mapping_set = ColumnMappingSet.from_dict(overrides)
    result = mapping_set.to_dict()

    for key, value in result.items():
        if value is not None:
            # Value should be the stripped version of the input
            assert value == overrides[key].strip()


@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    values=st.sampled_from(["", "   ", "\t\n"]),
    min_size=1,
    max_size=5,
))
def test_column_mapping_empty_values_become_none(overrides: dict[str, str]) -> None:
    """Empty or whitespace-only CDE selections normalize to None (skipped columns)."""
    mapping_set = ColumnMappingSet.from_dict(overrides)
    result = mapping_set.to_dict()

    # All empty/whitespace values should become None
    for value in result.values():
        assert value is None


@settings(max_examples=50)
@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=30).filter(lambda s: s.strip()),
    values=st.one_of(
        st.text(min_size=1, max_size=30).filter(lambda s: s.strip()),
        st.just(""),
    ),
    min_size=0,
    max_size=10,
))
def test_column_mapping_applied_plus_skipped_equals_total(overrides: dict[str, str]) -> None:
    """Applied mappings + skipped columns = total columns."""
    mapping_set = ColumnMappingSet.from_dict(overrides)

    applied = mapping_set.get_applied()
    skipped = mapping_set.get_skipped()

    # No overlap
    applied_cols = {m.column_name for m in applied}
    skipped_cols = set(skipped)
    assert applied_cols.isdisjoint(skipped_cols)

    # Together they cover all input columns
    assert applied_cols | skipped_cols == set(overrides.keys())


# =============================================================================
# Demo Bypass CDE Lookup Properties
# =============================================================================


@given(st.sampled_from(list(DEMO_CDE_REGISTRY.keys())))
def test_demo_cde_lookup_is_exact_match(key: str) -> None:
    """Registry lookup uses exact string matching — no normalization."""
    _ = DEMO_CDE_REGISTRY[key]  # Confirm key exists
    # Altering case must miss
    altered = key.swapcase()
    if altered != key:
        assert altered not in DEMO_CDE_REGISTRY, (
            f"Registry matched case-altered key '{altered}' — violates exact-match domain rule"
        )


@given(st.text(min_size=1, max_size=50))
def test_demo_cde_lookup_rejects_unknown_keys(key: str) -> None:
    """Keys not in the registry return None (no fuzzy matching)."""
    assume(key not in DEMO_CDE_REGISTRY)
    assert DEMO_CDE_REGISTRY.get(key) is None


# =============================================================================
# Manifest Normalization Properties
# =============================================================================


@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=30),
    values=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=30),
        min_size=1,
        max_size=3,
    ),
    min_size=0,
    max_size=5,
))
def test_normalize_manifest_preserves_valid_entries(column_mappings: dict[str, dict[str, str]]) -> None:
    """Valid Mapping entries with string keys survive normalization."""
    manifest = {"column_mappings": column_mappings}
    result = _normalize_manifest(manifest)
    result_mappings = result.get("column_mappings", {})

    # All string-keyed Mapping entries should survive
    for key in column_mappings:
        assert key in result_mappings, f"Valid entry '{key}' was dropped"


@given(st.one_of(
    st.just(42),
    st.just("not a mapping"),
    st.just(None),
    st.just([1, 2, 3]),
))
def test_normalize_manifest_rejects_non_mapping(bad_input: object) -> None:
    """Non-Mapping inputs produce empty column_mappings."""
    result = _normalize_manifest(bad_input)
    assert result.get("column_mappings", {}) == {}
