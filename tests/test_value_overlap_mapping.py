from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_profile import build_column_profile
from src.domain.columns import column_key_from_string
from src.domain.value_overlap_mapping import suggest_value_overlap_mappings


def test_includes_two_percent_and_excludes_values_below_it() -> None:
    # Given one source column at exactly 2% and one at about 1.99%.
    profiles = {
        "at_threshold": build_column_profile("at_threshold", (str(value) for value in range(200))),
        "below_threshold": build_column_profile("below_threshold", (str(value) for value in range(201))),
    }
    pvs = CdePvCatalog.from_mapping(
        {
            "target": frozenset({"0", "1", "2", "3"}),
        }
    )

    # When value-overlap suggestions are calculated.
    suggestions = suggest_value_overlap_mappings(profiles, pvs)

    # Then 4/200 is included and 4/201 is excluded.
    assert [
        candidate.cde_key
        for candidate in suggestions.by_column[column_key_from_string("at_threshold")]
    ] == ["target"]
    assert column_key_from_string("below_threshold") not in suggestions.by_column


def test_uses_exact_distinct_non_empty_values() -> None:
    # Given duplicate, empty, case-different, and whitespace-different source values.
    profile = build_column_profile("column_0", ["Alpha", "Alpha", "", "alpha", " Alpha "])
    pvs = CdePvCatalog.from_mapping({"target": frozenset({"Alpha", "alpha"})})

    # When suggestions are calculated.
    suggestions = suggest_value_overlap_mappings({"column_0": profile}, pvs)

    # Then duplicates and empty values do not affect the exact-match evidence.
    candidate = suggestions.by_column[column_key_from_string("column_0")][0]
    assert candidate.matched_value_count == 2
    assert candidate.source_value_count == 3
    assert candidate.overlap_ratio == 2 / 3


def test_returns_at_most_five_in_stable_evidence_order() -> None:
    # Given more than five qualifying CDEs, including equal scores.
    profile = build_column_profile("column_0", ["a", "b", "c", "d"])
    pvs = CdePvCatalog.from_mapping(
        {
            "z": frozenset({"a", "b", "c"}),
            "b": frozenset({"a", "b"}),
            "a": frozenset({"a", "b"}),
            "c": frozenset({"a"}),
            "d": frozenset({"a"}),
            "e": frozenset({"a"}),
            "f": frozenset({"a"}),
        }
    )

    # When suggestions are calculated twice.
    first = suggest_value_overlap_mappings({"column_0": profile}, pvs)
    second = suggest_value_overlap_mappings({"column_0": profile}, pvs)

    # Then both results contain the same top five in evidence order.
    expected = ["z", "a", "b", "c", "d"]
    assert [candidate.cde_key for candidate in first.by_column[column_key_from_string("column_0")]] == expected
    assert first == second


def test_omits_empty_source_columns_and_empty_value_sets() -> None:
    # Given an empty source profile and a CDE with an explicit empty PV set.
    profiles = {
        "empty": build_column_profile("empty", ["", ""]),
        "valued": build_column_profile("valued", ["x"]),
    }
    pvs = CdePvCatalog.from_mapping({"passthrough": frozenset()})

    # When suggestions are calculated.
    suggestions = suggest_value_overlap_mappings(profiles, pvs)

    # Then neither column has a suggestion.
    assert suggestions.by_column == {}
