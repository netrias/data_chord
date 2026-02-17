"""Tests for data_model_adapter — SDK-to-domain type conversion and graceful degradation."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from netrias_client import DataModel, DataModelStoreError, DataModelVersion

from src.domain.data_model_adapter import (
    fetch_pvs_batch_async,
    get_latest_version,
    list_data_model_summaries,
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
    assert summaries[0].versions == ["1", "2"]
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
async def test_fetch_pvs_batch_degrades_on_per_key_failure(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: a mock client where "age" succeeds but "bad_key" raises DataModelStoreError
    When: fetch_pvs_batch_async() is called with both keys
    Then: "age" has its PVs and "bad_key" degrades to empty frozenset
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

    # Then: successful key has PVs, failed key degrades to empty
    assert result["age"] == age_pvs, f"age PVs mismatch: {result['age']}"
    assert result["bad_key"] == frozenset(), (
        f"Expected empty frozenset for bad_key, got {result['bad_key']}"
    )


# ---------------------------------------------------------------------------
# Test: fetch_pvs_batch_async returns {} when client is None
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
