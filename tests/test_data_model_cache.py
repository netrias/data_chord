"""Tests for populate_cde_cache — real CDE fetching from Data Model Store API."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from netrias_client import CDE as SdkCDE
from netrias_client import DataModel, DataModelStoreError, DataModelVersion

from src.domain.data_model_cache import clear_session_cache, get_session_cache, populate_cde_cache

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
    populate_cde_cache("test-file-id", "gc")

    # Then
    assert cache.has_cdes()
    assert len(cache.get_all_cdes()) == 3
    assert cache.get_cde_by_key("age") is not None
    assert cache.get_cde_by_key("sex") is not None
    assert cache.get_cde_by_key("race") is not None

    data_model_key, version_label = cache.get_model_info()
    assert data_model_key == "gc"
    assert version_label == "2"

    mock_netrias.list_cdes.assert_called_once_with("gc", "2", include_description=True)


# ---------------------------------------------------------------------------
# Test: populate_cde_cache falls back on version error
# ---------------------------------------------------------------------------


def test_populate_cde_cache_falls_back_on_version_error(
    mock_netrias: MagicMock,
) -> None:
    """
    Given: list_data_models raises DataModelStoreError (version lookup fails)
           and no CDEs are cached yet
    When: populate_cde_cache() is called
    Then: CDEs are fetched with version_label="1" (fallback)
    """
    # Given: version lookup fails
    mock_netrias.list_data_models.side_effect = DataModelStoreError("unavailable")
    mock_netrias.list_cdes.return_value = (
        SdkCDE(cde_key="age", cde_id=1, cde_version_id=1, description="Age"),
    )

    cache = get_session_cache("test-file-id")
    assert not cache.has_cdes()

    # When
    populate_cde_cache("test-file-id", "gc")

    # Then: fallback version "1" was used
    mock_netrias.list_cdes.assert_called_once_with("gc", "1", include_description=True)
    assert cache.has_cdes()

    _, version_label = cache.get_model_info()
    assert version_label == "1"
