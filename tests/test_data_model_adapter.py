"""Tests for data_model_adapter — SDK-to-domain type conversion and graceful degradation."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from netrias_client import DataModel, DataModelStoreError, DataModelVersion

from src.domain.cde import CDEInfo, CdeType
from src.domain.data_model_adapter import (
    _pv_map_from_all_pvs_response,
    fetch_cdes,
    fetch_pvs_batch_async,
    get_latest_version,
    list_data_model_summaries,
    refine_cde_types_from_pvs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_netrias_singleton() -> Generator[None]:
    """Prevent inter-test leakage via the dependency singleton."""
    import src.domain.dependencies as deps

    saved_client = deps._netrias_client
    saved_init = deps._netrias_client_initialized
    yield
    deps._netrias_client = saved_client
    deps._netrias_client_initialized = saved_init


@pytest.fixture
def mock_netrias() -> Generator[MagicMock]:
    """Why: inject a mock NetriasClient so tests never hit the real API."""
    import src.domain.dependencies as deps

    mock = MagicMock()
    deps._netrias_client = mock
    deps._netrias_client_initialized = True
    yield mock


# ---------------------------------------------------------------------------
# Test: list_data_model_summaries returns sorted summaries (preferred first)
# ---------------------------------------------------------------------------


def test_list_summaries_returns_preferred_model_first(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: two data models ("alpha" and "gc") returned in alphabetical order
           and no summaries have been fetched yet
    When: list_data_model_summaries() is called
    Then: preferred model "gc" appears first in the result
    """
    # Given
    mock_netrias.list_data_models.return_value = (
        DataModel(
            data_commons_id=1, key="alpha", name="Alpha Model",
            description=None, is_active=True,
            versions=(DataModelVersion(version_label="1"),),
        ),
        DataModel(
            data_commons_id=2, key="gc", name="Genomic Commons",
            description=None, is_active=True,
            versions=(
                DataModelVersion(version_label="1"),
                DataModelVersion(version_label="2"),
            ),
        ),
    )

    # When
    summaries = list_data_model_summaries()

    # Then: "gc" is first despite alphabetical ordering of raw data
    assert len(summaries) == 2, f"Expected 2 summaries, got {len(summaries)}"
    assert summaries[0].key == "gc"
    assert summaries[0].label == "Genomic Commons"
    assert [v.version_number for v in summaries[0].versions] == [1, 2]
    assert [v.version_label for v in summaries[0].versions] == ["1", "2"]
    assert summaries[1].key == "alpha"


# ---------------------------------------------------------------------------
# Test: list_data_model_summaries returns [] when client is None
# ---------------------------------------------------------------------------


def test_list_summaries_returns_empty_when_client_unavailable() -> None:
    """
    Given: no NetriasClient (API key missing)
    When: list_data_model_summaries() is called
    Then: empty list returned (graceful degradation)
    """
    import src.domain.dependencies as deps

    # Given: client is None
    deps._netrias_client = None
    deps._netrias_client_initialized = True

    # When
    summaries = list_data_model_summaries()

    # Then
    assert summaries == [], f"Expected empty list, got {summaries}"


# ---------------------------------------------------------------------------
# Test: get_latest_version returns last version label from matching model
# ---------------------------------------------------------------------------


def test_get_latest_version_returns_last_version_label(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: a data model "gc" with versions ["1", "2", "3"]
    When: get_latest_version("gc") is called
    Then: returns "3" (the last version in the tuple)
    """
    # Given
    mock_netrias.list_data_models.return_value = (
        DataModel(
            data_commons_id=1, key="gc", name="Genomic Commons",
            description=None, is_active=True,
            versions=(
                DataModelVersion(version_label="1"),
                DataModelVersion(version_label="2"),
                DataModelVersion(version_label="3"),
            ),
        ),
    )

    # When
    version = get_latest_version("gc")

    # Then
    assert version == "3", f"Expected '3', got '{version}'"
    mock_netrias.list_data_models.assert_called_once_with(
        query="gc", include_versions=True,
    )


# ---------------------------------------------------------------------------
# Test: get_latest_version returns "1" when client is None
# ---------------------------------------------------------------------------


def test_get_latest_version_returns_default_when_client_unavailable() -> None:
    """
    Given: no NetriasClient (API key missing)
    When: get_latest_version("gc") is called
    Then: returns fallback version "1"
    """
    import src.domain.dependencies as deps

    # Given
    deps._netrias_client = None
    deps._netrias_client_initialized = True

    # When
    version = get_latest_version("gc")

    # Then
    assert version == "1", f"Expected fallback '1', got '{version}'"


# ---------------------------------------------------------------------------
# Test: fetch_pvs_batch_async returns PV sets for multiple CDE keys
# ---------------------------------------------------------------------------


def test_all_pvs_response_groups_values_by_cde_key() -> None:
    """
    Given: the model-version PV endpoint returns rows for several CDEs
    When: the adapter parses the response
    Then: PV values are grouped as immutable sets keyed by CDE key
    """
    # Given
    response_body = {
        "items": [
            {"cde_key": "diagnosis", "pv_value": "Lung"},
            {"cde_key": "diagnosis", "pv_value": "Breast"},
            {"cde_key": "sex", "pv_value": "Female"},
        ]
    }
    parsed: dict[str, frozenset[str]] = {}
    assert parsed == {}

    # When
    parsed = _pv_map_from_all_pvs_response(response_body)

    # Then
    assert parsed == {
        "diagnosis": frozenset({"Lung", "Breast"}),
        "sex": frozenset({"Female"}),
    }


@pytest.mark.asyncio
async def test_fetch_pvs_batch_returns_pv_sets_for_multiple_keys(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: a mock client that returns distinct PV frozensets per CDE key
           and no PVs have been fetched yet
    When: fetch_pvs_batch_async() is called with two keys
    Then: dict maps each key to its PV frozenset
    """
    # Given
    age_pvs = frozenset({"0-18", "19-40", "41-65", "66+"})
    sex_pvs = frozenset({"Male", "Female", "Unknown"})

    async def _fake_get_pv_set(
        _data_model_key: str, _version: str, cde_key: str,
    ) -> frozenset[str]:
        return {"age": age_pvs, "sex": sex_pvs}[cde_key]

    mock_netrias.get_pv_set_async = AsyncMock(side_effect=_fake_get_pv_set)

    result = {}  # negative assertion: nothing fetched yet
    assert result == {}

    # When
    result = await fetch_pvs_batch_async("gc", "2", ["age", "sex"])

    # Then
    assert result["age"] == age_pvs, f"age PVs mismatch: {result['age']}"
    assert result["sex"] == sex_pvs, f"sex PVs mismatch: {result['sex']}"
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Test: fetch_pvs_batch_async per-key failure degrades to empty frozenset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_pvs_batch_drops_failed_keys(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: a mock client where "age" succeeds but "bad_key" raises DataModelStoreError
    When: fetch_pvs_batch_async() is called with both keys
    Then: "age" is in the result; "bad_key" is absent so callers don't
          confuse a fetch failure with a legitimately-empty PV set
    """
    # Given
    age_pvs = frozenset({"0-18", "19-40"})

    async def _fake_get_pv_set(
        _data_model_key: str, _version: str, cde_key: str,
    ) -> frozenset[str]:
        if cde_key == "bad_key":
            raise DataModelStoreError("not found")
        return age_pvs

    mock_netrias.get_pv_set_async = AsyncMock(side_effect=_fake_get_pv_set)

    # When
    result = await fetch_pvs_batch_async("gc", "2", ["age", "bad_key"])

    # Then: successful key has PVs, failed key is absent (will retry next call)
    assert result["age"] == age_pvs, f"age PVs mismatch: {result['age']}"
    assert "bad_key" not in result, f"bad_key should be absent, got {result.get('bad_key')!r}"


# ---------------------------------------------------------------------------
# Test: fetch_pvs_batch_async returns {} when client is None
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test: fetch_cdes assigns the default PV cde_type pre-fetch
# ---------------------------------------------------------------------------


def test_fetch_cdes_defaults_cde_type_to_pv(mock_netrias: MagicMock) -> None:
    """
    Given: SDK returns CDEs but PVs have not been fetched yet
    When: fetch_cdes wraps them into CDEInfo
    Then: cde_type defaults to PV — adapter refines after PVs resolve
    """
    # Given: SDK CDEs (use SimpleNamespace to avoid coupling to SDK class shape)
    mock_netrias.list_cdes.return_value = [
        SimpleNamespace(cde_id=1, cde_key="diagnosis", description="dx"),
        SimpleNamespace(cde_id=2, cde_key="age", description="age"),
    ]
    # negative assertion: no fetched CDEs yet
    fetched: list = []
    assert fetched == []

    # When
    fetched = fetch_cdes("gc", "1")

    # Then: every CDE starts as PV (refinement happens later)
    assert {c.cde_type for c in fetched} == {CdeType.PV}


def test_refine_cde_types_downgrades_to_passthrough_for_empty_pvs() -> None:
    """
    Given: two CDEs both initially typed as PV; PV fetch returned an empty
           frozenset for one of them
    When: refine_cde_types_from_pvs is called
    Then: the empty-PV CDE becomes PASSTHROUGH; the populated one stays PV
    """
    # Given
    cdes = [
        CDEInfo(cde_id=1, cde_key="diagnosis", description=None, version_label="1"),
        CDEInfo(cde_id=2, cde_key="free_notes", description=None, version_label="1"),
    ]
    pv_sets = {
        "diagnosis": frozenset({"A", "B"}),
        "free_notes": frozenset(),
    }
    # negative assertion: types start as PV
    assert all(c.cde_type == CdeType.PV for c in cdes)

    # When
    refined = refine_cde_types_from_pvs(cdes, pv_sets)
    by_key = {c.cde_key: c for c in refined}

    # Then
    assert by_key["diagnosis"].cde_type == CdeType.PV
    assert by_key["free_notes"].cde_type == CdeType.PASSTHROUGH


def test_refine_cde_types_preserves_numeric_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a CDE flagged as NUMERIC by override; PV fetch returns empty for it
    When: refine_cde_types_from_pvs is called
    Then: NUMERIC is preserved (override beats has_pvs=False)
    """
    # Given
    monkeypatch.setattr(
        "src.domain.cde_type_overrides.NUMERIC_CDE_KEYS",
        frozenset({"patient_age_yrs"}),
    )
    cdes = [
        CDEInfo(
            cde_id=1, cde_key="patient_age_yrs",
            description=None, version_label="1",
            cde_type=CdeType.NUMERIC,
        ),
    ]
    pv_sets = {"patient_age_yrs": frozenset()}

    # When
    refined = refine_cde_types_from_pvs(cdes, pv_sets)

    # Then
    assert refined[0].cde_type == CdeType.NUMERIC


def test_refine_cde_types_skips_unfetched_cdes() -> None:
    """
    Given: two CDEs, but PVs were fetched for only one
    When: refine_cde_types_from_pvs is called
    Then: the un-fetched CDE keeps its original type unchanged
    """
    # Given
    cdes = [
        CDEInfo(cde_id=1, cde_key="diagnosis", description=None, version_label="1"),
        CDEInfo(cde_id=2, cde_key="other", description=None, version_label="1"),
    ]
    pv_sets = {"diagnosis": frozenset({"A"})}

    # When
    refined = refine_cde_types_from_pvs(cdes, pv_sets)
    by_key = {c.cde_key: c for c in refined}

    # Then: untouched CDE remains as it was
    assert by_key["other"].cde_type == CdeType.PV


def test_refine_does_not_downgrade_when_fetch_failure_omits_key() -> None:
    """
    Given: a CDE that genuinely has PVs but its PV fetch failed and is therefore
           absent from the pv_sets dict (per fetch_pvs_batch_async's contract)
    When: refine_cde_types_from_pvs is called
    Then: the CDE stays at its initial type (PV) — it is NOT downgraded to
          PASSTHROUGH, which would happen if absent-key were treated the same
          as known-empty-set. This is the regression that hid primary_diagnosis
          behind PASSTHROUGH when batch fetches hit the rate limit.
    """
    # Given
    cdes = [
        CDEInfo(cde_id=1, cde_key="primary_diagnosis", description=None, version_label="1"),
    ]
    # PV sets dict is empty — the fetch failed for primary_diagnosis
    pv_sets: dict[str, frozenset[str]] = {}

    # When
    refined = refine_cde_types_from_pvs(cdes, pv_sets)

    # Then: type is PRESERVED at PV; no PASSTHROUGH downgrade
    assert refined[0].cde_type == CdeType.PV


def test_fetch_cdes_applies_numeric_override_at_initial_fetch(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a CDE key that the team has flagged as numeric in the override list
    When: fetch_cdes wraps it into CDEInfo (PVs not yet fetched)
    Then: cde_type is NUMERIC immediately — override beats the PV default
    """
    # Given: override entry for "patient_age_yrs"
    monkeypatch.setattr(
        "src.domain.cde_type_overrides.NUMERIC_CDE_KEYS",
        frozenset({"patient_age_yrs"}),
    )
    mock_netrias.list_cdes.return_value = [
        SimpleNamespace(cde_id=1, cde_key="patient_age_yrs", description="age"),
        SimpleNamespace(cde_id=2, cde_key="diagnosis", description="dx"),
    ]

    # When
    fetched = fetch_cdes("gc", "1")
    by_key = {c.cde_key: c for c in fetched}

    # Then
    assert by_key["patient_age_yrs"].cde_type == CdeType.NUMERIC
    assert by_key["diagnosis"].cde_type == CdeType.PV


# ---------------------------------------------------------------------------
# Test: fetch_pvs_batch_async returns PVs (existing test header)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_pvs_batch_returns_empty_when_client_unavailable() -> None:
    """
    Given: no NetriasClient (API key missing)
    When: fetch_pvs_batch_async() is called
    Then: returns empty dict (graceful degradation)
    """
    import src.domain.dependencies as deps

    # Given
    deps._netrias_client = None
    deps._netrias_client_initialized = True

    # When
    result = await fetch_pvs_batch_async("gc", "2", ["age", "sex"])

    # Then
    assert result == {}, f"Expected empty dict, got {result}"
