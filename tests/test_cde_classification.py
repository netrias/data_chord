"""Tests for CdeType enum and the cde_type_overrides classifier.

The classifier owns CDE type assignment: PV (default), NUMERIC (overridden or
heuristic), PASSTHROUGH (no PVs after fetch).
"""

from __future__ import annotations

import pytest

from src.domain.cde import CDEInfo, CdeType, is_rename_only
from src.domain.cde_type_overrides import NUMERIC_CDE_KEYS, classify_cde

# ---------------------------------------------------------------------------
# Test: CdeType has the three states the takeover branches on
# ---------------------------------------------------------------------------


def test_cde_type_has_three_named_states() -> None:
    """
    Given: the CdeType enum
    When: we read its values
    Then: only the three documented states exist (pv, numeric, passthrough)
    """
    # Given / When
    values = {ct.value for ct in CdeType}

    # Then: no ad-hoc fourth state has slipped in
    assert values == {"pv", "numeric", "passthrough"}, f"Unexpected types: {values}"


# ---------------------------------------------------------------------------
# Test: CDEInfo defaults cde_type to PV when unspecified
# ---------------------------------------------------------------------------


def test_cde_info_defaults_to_pv_type() -> None:
    """
    Given: a CDEInfo constructed without cde_type
    When: we read its cde_type field
    Then: it defaults to PV (existing-code behavior preserved)
    """
    # Given: no cde_type was passed explicitly (negative assertion: not yet refined)
    info = CDEInfo(cde_id=1, cde_key="diagnosis", description=None, version_label="1")
    assert info.cde_type != CdeType.NUMERIC

    # When / Then
    assert info.cde_type == CdeType.PV


def test_cde_info_accepts_explicit_type() -> None:
    """
    Given: a CDEInfo constructed with cde_type=NUMERIC
    When: we read its cde_type field
    Then: the explicit value wins
    """
    # Given
    info = CDEInfo(
        cde_id=2,
        cde_key="age",
        description="age in years",
        version_label="1",
        cde_type=CdeType.NUMERIC,
    )

    # When / Then
    assert info.cde_type == CdeType.NUMERIC


def test_is_rename_only_collapses_numeric_and_passthrough() -> None:
    """
    Given: the three CDE validation types
    When: the rename-only predicate is evaluated
    Then: only NUMERIC and PASSTHROUGH collapse to rename-only
    """
    # Given: no type has been classified yet in this test
    results: dict[CdeType, bool] = {}
    assert results == {}

    # When
    results = {cde_type: is_rename_only(cde_type) for cde_type in CdeType}

    # Then
    assert results == {
        CdeType.PV: False,
        CdeType.NUMERIC: True,
        CdeType.PASSTHROUGH: True,
    }


# ---------------------------------------------------------------------------
# Test: classify_cde branches by precedence — override > pv-presence > heuristic
# ---------------------------------------------------------------------------


def test_classify_cde_returns_numeric_when_key_in_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a CDE key listed in NUMERIC_CDE_KEYS and known to have PVs (would
           otherwise classify as PV)
    When: classify_cde is called
    Then: NUMERIC wins — override beats PV presence
    """
    # Given: override entry for "patient_age_yrs"
    monkeypatch.setattr(
        "src.domain.cde_type_overrides.NUMERIC_CDE_KEYS",
        frozenset({"patient_age_yrs"}),
    )

    # When
    result = classify_cde("patient_age_yrs", has_pvs=True, sample_is_numeric=False)

    # Then: override > everything else
    assert result == CdeType.NUMERIC


def test_classify_cde_returns_passthrough_when_pvs_known_empty() -> None:
    """
    Given: a CDE not in the override list, with PVs fetched and confirmed empty
    When: classify_cde is called
    Then: PASSTHROUGH (the canonical "no validation" type)
    """
    # Given: no override; has_pvs=False means we've fetched and got empty
    assert "free_notes" not in NUMERIC_CDE_KEYS

    # When
    result = classify_cde("free_notes", has_pvs=False, sample_is_numeric=False)

    # Then
    assert result == CdeType.PASSTHROUGH


def test_classify_cde_returns_pv_when_pvs_present() -> None:
    """
    Given: a CDE with PVs fetched and non-empty
    When: classify_cde is called
    Then: PV (the default "must match a permissible value" type)
    """
    # When
    result = classify_cde("diagnosis", has_pvs=True, sample_is_numeric=False)

    # Then
    assert result == CdeType.PV


def test_classify_cde_uses_numeric_heuristic_when_pvs_unknown() -> None:
    """
    Given: PVs not yet fetched (has_pvs=None) and the column data parses as numeric
    When: classify_cde is called
    Then: NUMERIC — pre-fetch heuristic kicks in so the takeover renders correctly
    """
    # Given: PVs not yet fetched
    # When
    result = classify_cde("unknown_cde", has_pvs=None, sample_is_numeric=True)

    # Then
    assert result == CdeType.NUMERIC


def test_classify_cde_defaults_to_pv_when_pvs_unknown_and_non_numeric() -> None:
    """
    Given: PVs not yet fetched (has_pvs=None) and the column is non-numeric
    When: classify_cde is called
    Then: PV — best guess until the adapter refines after a fetch
    """
    # When
    result = classify_cde("unknown_cde", has_pvs=None, sample_is_numeric=False)

    # Then
    assert result == CdeType.PV
