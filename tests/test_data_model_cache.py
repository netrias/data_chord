"""Tests for populate_cde_cache — real CDE fetching from Data Model Store API."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.domain.cde import CDEInfo
from src.domain.data_model_cache import clear_session_cache, get_session_cache, populate_cde_cache
from src.domain.data_model_client import DataModelClientError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    """Prevent inter-test leakage via the global session cache."""
    clear_session_cache("test-file-id")


@pytest.fixture
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_MODEL_KEY", "gc")


@pytest.fixture
def mock_dm_client() -> MagicMock:
    client = MagicMock()
    client.get_latest_version.return_value = "2"
    client.fetch_cdes.return_value = [
        CDEInfo(cde_id=1, cde_key="age", description="Age", version_label="2"),
        CDEInfo(cde_id=2, cde_key="sex", description="Sex", version_label="2"),
        CDEInfo(cde_id=3, cde_key="race", description="Race", version_label="2"),
    ]
    return client


# ---------------------------------------------------------------------------
# Test 6: populate_cde_cache stores real CDEs
# ---------------------------------------------------------------------------


def test_populate_cde_cache_stores_real_cdes(
    _env: None,
    mock_dm_client: MagicMock,
) -> None:
    """
    Given: a mocked DataModelClient returning 3 CDEs
           and the session cache has no CDEs
    When: populate_cde_cache() is called
    Then: session cache contains all 3 CDEs with correct model info
    """
    # Given
    cache = get_session_cache("test-file-id")
    assert not cache.has_cdes()

    # When
    populate_cde_cache("test-file-id", mock_dm_client)

    # Then
    assert cache.has_cdes()
    assert len(cache.get_all_cdes()) == 3
    assert cache.get_cde_by_key("age") is not None
    assert cache.get_cde_by_key("sex") is not None
    assert cache.get_cde_by_key("race") is not None

    data_model_key, version_label = cache.get_model_info()
    assert data_model_key == "gc"
    assert version_label == "2"

    mock_dm_client.fetch_cdes.assert_called_once_with("gc", "2")


# ---------------------------------------------------------------------------
# Test 7: populate_cde_cache falls back on version error
# ---------------------------------------------------------------------------


def test_populate_cde_cache_falls_back_on_version_error(
    _env: None,
    mock_dm_client: MagicMock,
) -> None:
    """
    Given: a DataModelClient that raises on get_latest_version
           and no CDEs are cached yet
    When: populate_cde_cache() is called
    Then: CDEs are fetched with version_label="1" (fallback)
    """
    # Given: version lookup fails
    mock_dm_client.get_latest_version.side_effect = DataModelClientError("unavailable")
    mock_dm_client.fetch_cdes.return_value = [
        CDEInfo(cde_id=1, cde_key="age", description="Age", version_label="1"),
    ]

    cache = get_session_cache("test-file-id")
    assert not cache.has_cdes()

    # When
    populate_cde_cache("test-file-id", mock_dm_client)

    # Then: fallback version "1" was used
    mock_dm_client.fetch_cdes.assert_called_once_with("gc", "1")
    assert cache.has_cdes()

    _, version_label = cache.get_model_info()
    assert version_label == "1"
