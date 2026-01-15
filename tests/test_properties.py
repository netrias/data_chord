"""
Property-based tests for domain logic using Hypothesis.

Tests invariants across randomly generated inputs rather than specific examples.
Complements existing unit tests by exploring edge cases automatically.
"""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.domain.cde import (
    TARGET_ALIAS_MAP,
    CDEField,
    ColumnMappingSet,
    normalize_target_name,
)
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
    """If top_harmonization is in PV set, result is conformant with no adjustment."""
    pv_set = frozenset(pv_list)
    top = pv_list[0]  # Use a known member

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
    """If top is non-conformant but a suggestion is, adjustment is made."""
    pv_set = frozenset(suggestion_pvs)
    assume(non_conformant_top not in pv_set)
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
# CDE Normalization Properties
# =============================================================================


@given(st.sampled_from(list(TARGET_ALIAS_MAP.keys())))
def test_normalize_known_aliases_resolve(alias: str) -> None:
    """Known aliases in TARGET_ALIAS_MAP always resolve."""
    result = normalize_target_name(alias)
    assert result is not None
    assert isinstance(result, CDEField)


@given(st.text().filter(lambda s: s.strip()))
def test_normalize_is_idempotent_for_resolved_values(text: str) -> None:
    """If normalization resolves, re-normalizing the value produces same result."""
    first = normalize_target_name(text)
    if first is not None:
        # The enum value should also resolve to itself
        second = normalize_target_name(first.value)
        assert second == first


@given(st.sampled_from([None, "", "   ", "\t\n"]))
def test_normalize_empty_returns_none(empty: str | None) -> None:
    """Empty or whitespace-only input returns None."""
    assert normalize_target_name(empty) is None


@given(st.text().filter(lambda s: "_".join(s.strip().lower().replace("-", " ").split()) not in TARGET_ALIAS_MAP))
def test_normalize_unknown_returns_none(unknown: str) -> None:
    """Values not in alias map return None."""
    assume(unknown.strip())
    assert normalize_target_name(unknown) is None


# =============================================================================
# ColumnMappingSet Roundtrip Properties
# =============================================================================


@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    values=st.sampled_from(list(TARGET_ALIAS_MAP.keys())),
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
    values=st.sampled_from(list(TARGET_ALIAS_MAP.keys())),
    min_size=1,
    max_size=10,
))
def test_column_mapping_values_are_normalized(overrides: dict[str, str]) -> None:
    """All values are normalized to CDEField enum values."""
    mapping_set = ColumnMappingSet.from_dict(overrides)
    result = mapping_set.to_dict()

    for value in result.values():
        if value is not None:
            # Should be a valid CDEField value
            assert value in [f.value for f in CDEField]


@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    values=st.sampled_from(["invalid_cde", "not_a_real_field", "xyz"]),
    min_size=1,
    max_size=5,
))
def test_column_mapping_invalid_values_become_none(overrides: dict[str, str]) -> None:
    """Invalid CDE selections normalize to None (skipped columns)."""
    mapping_set = ColumnMappingSet.from_dict(overrides)
    result = mapping_set.to_dict()

    # All invalid values should become None
    for value in result.values():
        assert value is None


@settings(max_examples=50)
@given(st.dictionaries(
    keys=st.text(min_size=1, max_size=30).filter(lambda s: s.strip()),
    values=st.sampled_from(list(TARGET_ALIAS_MAP.keys()) + ["invalid"]),
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
