"""Tests for CdeType enum and the CDE type classifier.

The classifier owns CDE type assignment: PV by default, PASSTHROUGH when a
fetched CDE has no permissible values.
"""

from __future__ import annotations

from src.domain.cde import CDEInfo, CdeType, is_rename_only
from src.domain.cde_type_classification import classify_cde

# ---------------------------------------------------------------------------
# Test: CdeType has the two states the takeover branches on
# ---------------------------------------------------------------------------


def test_cde_type_has_two_named_states() -> None:
    """
    Given: the CdeType enum
    When: we read its values
    Then: only the two documented states exist (pv, passthrough)
    """
    # Given / When
    values = {ct.value for ct in CdeType}

    # Then: no ad-hoc third state has slipped in
    assert values == {"pv", "passthrough"}, f"Unexpected types: {values}"


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
    info = CDEInfo(cde_id=1, cde_key="diagnosis", description=None)
    assert info.cde_type != CdeType.PASSTHROUGH

    # When / Then
    assert info.cde_type == CdeType.PV


def test_cde_info_accepts_explicit_type() -> None:
    """
    Given: a CDEInfo constructed with cde_type=PASSTHROUGH
    When: we read its cde_type field
    Then: the explicit value wins
    """
    # Given
    info = CDEInfo(
        cde_id=2,
        cde_key="notes",
        description="free-text notes",
        cde_type=CdeType.PASSTHROUGH,
    )

    # When / Then
    assert info.cde_type == CdeType.PASSTHROUGH


def test_is_rename_only_returns_true_for_passthrough_only() -> None:
    """
    Given: the two CDE validation types
    When: the rename-only predicate is evaluated
    Then: only PASSTHROUGH collapses to rename-only
    """
    # Given: no type has been classified yet in this test
    results: dict[CdeType, bool] = {}
    assert results == {}

    # When
    results = {cde_type: is_rename_only(cde_type) for cde_type in CdeType}

    # Then
    assert results == {
        CdeType.PV: False,
        CdeType.PASSTHROUGH: True,
    }


# ---------------------------------------------------------------------------
# Test: classify_cde branches by PV presence
# ---------------------------------------------------------------------------


def test_classify_cde_returns_passthrough_when_pvs_known_empty() -> None:
    """
    Given: a CDE with PVs fetched and confirmed empty
    When: classify_cde is called
    Then: PASSTHROUGH (the canonical "no validation" type)
    """
    # Given: has_pvs=False means we've fetched and got empty
    has_pvs = False
    assert not has_pvs

    # When
    result = classify_cde(has_pvs=has_pvs)

    # Then
    assert result == CdeType.PASSTHROUGH


def test_classify_cde_returns_pv_when_pvs_present() -> None:
    """
    Given: a CDE with PVs fetched and non-empty
    When: classify_cde is called
    Then: PV (the default "must match a permissible value" type)
    """
    # When
    result = classify_cde(has_pvs=True)

    # Then
    assert result == CdeType.PV


def test_classify_cde_defaults_to_pv_when_pvs_unknown() -> None:
    """
    Given: PVs not yet fetched (has_pvs=None)
    When: classify_cde is called
    Then: PV — best guess until the adapter refines after PV lookup
    """
    # When
    result = classify_cde(has_pvs=None)

    # Then
    assert result == CdeType.PV
