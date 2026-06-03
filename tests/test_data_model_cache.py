"""Tests for populate_cde_cache — real CDE fetching from Data Model Store API."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from netrias_client import CDE as SdkCDE
from netrias_client import DataModel, DataModelVersion

from src.domain.data_model_cache import clear_session_cache, get_session_cache, populate_cde_cache
from src.domain.data_model_selection import DataModelSelection

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    """Prevent inter-test leakage via the global session cache."""
    clear_session_cache("test-file-id")


@pytest.fixture
def mock_netrias() -> Generator[MagicMock]:
    """Why: inject a mock NetriasClient into the adapter singleton."""
    import src.domain.dependencies as deps

    mock = MagicMock()
    mock.list_data_models.return_value = (
        DataModel(
            data_commons_id=1, key="gc", name="Genomic Commons",
            description=None, is_active=True,
            versions=(DataModelVersion(version_label="2"),),
        ),
    )
    mock.list_cdes.return_value = (
        SdkCDE(cde_key="age", cde_id=1, cde_version_id=1, description="Age"),
        SdkCDE(cde_key="sex", cde_id=2, cde_version_id=1, description="Sex"),
        SdkCDE(cde_key="race", cde_id=3, cde_version_id=1, description="Race"),
    )

    saved = deps._netrias_client
    saved_init = deps._netrias_client_initialized
    deps._netrias_client = mock
    deps._netrias_client_initialized = True
    yield mock
    deps._netrias_client = saved
    deps._netrias_client_initialized = saved_init


# ---------------------------------------------------------------------------
# Test: populate_cde_cache stores real CDEs (TS-3)
# ---------------------------------------------------------------------------


def test_populate_cde_cache_stores_real_cdes(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: a mocked NetriasClient returning 3 CDEs for model "gc"
           and the session cache has no CDEs
    When: populate_cde_cache() is called with data_model_key="gc"
    Then: session cache contains all 3 CDEs with correct model info
    """
    # Given
    cache = get_session_cache("test-file-id")
    assert not cache.has_cdes()

    # When
    populate_cde_cache("test-file-id", DataModelSelection.from_external_version_number("gc", "2.0.0"))

    # Then
    assert cache.has_cdes()
    assert len(cache.get_all_cdes()) == 3
    assert cache.get_cde_by_key("age") is not None
    assert cache.get_cde_by_key("sex") is not None
    assert cache.get_cde_by_key("race") is not None

    selection = cache.get_model_selection()
    assert selection is not None
    assert selection.key == "gc"
    assert selection.external_version_number == "2.0.0"

    mock_netrias.list_cdes.assert_called_once_with(
        "gc",
        external_version_number="2.0.0",
        include_description=True,
    )


# ---------------------------------------------------------------------------
# Test: populate_cde_cache requires explicit external version
# ---------------------------------------------------------------------------


def test_populate_cde_cache_uses_explicit_external_version(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: an explicit external version and no CDEs are cached yet
    When: populate_cde_cache() is called
    Then: CDEs are fetched with that external version
    """
    # Given
    mock_netrias.list_cdes.return_value = (
        SdkCDE(cde_key="age", cde_id=1, cde_version_id=1, description="Age"),
    )

    cache = get_session_cache("test-file-id")
    assert not cache.has_cdes()

    # When
    populate_cde_cache("test-file-id", DataModelSelection.from_external_version_number("gc", "1.0.0"))

    # Then: the required external version is used directly; no latest fallback occurs
    mock_netrias.list_cdes.assert_called_once_with(
        "gc",
        external_version_number="1.0.0",
        include_description=True,
    )
    assert cache.has_cdes()

    selection = cache.get_model_selection()
    assert selection is not None
    assert selection.external_version_number == "1.0.0"
