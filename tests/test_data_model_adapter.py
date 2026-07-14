"""Tests for data_model_adapter — SDK-to-domain type conversion and graceful degradation."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from netrias_client import DataModel, DataModelStoreError, DataModelVersion

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.integrations.data_model_store import (
    _pv_map_from_all_pvs_response,
    fetch_all_pvs_async,
    fetch_cdes,
    list_data_model_summaries,
    refine_cde_types_from_pvs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_netrias_singleton() -> Generator[None]:
    """Prevent inter-test leakage via the dependency singleton."""
    import src.app.dependencies as deps

    saved_client = deps._netrias_client
    saved_init = deps._netrias_client_initialized
    yield
    deps._netrias_client = saved_client
    deps._netrias_client_initialized = saved_init


@pytest.fixture
def mock_netrias() -> Generator[MagicMock]:
    """Why: inject a mock NetriasClient so tests never hit the real API."""
    import src.app.dependencies as deps

    mock = MagicMock()
    deps._netrias_client = mock
    deps._netrias_client_initialized = True
    yield mock


# ---------------------------------------------------------------------------
# Test: list_data_model_summaries returns sorted summaries (preferred first)
# ---------------------------------------------------------------------------


def test_list_summaries_returns_preferred_model_first(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
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
            versions=(DataModelVersion(external_version_number="11.0.3"),),
        ),
        DataModel(
            data_commons_id=2, key="gc", name="Genomic Commons",
            description=None, is_active=True,
            versions=(
                DataModelVersion(external_version_number="11.0.3"),
                DataModelVersion(external_version_number="11.0.4"),
            ),
        ),
    )
    mock_netrias.settings = SimpleNamespace(
        data_model_store_endpoints=SimpleNamespace(base_url="https://dms.example.test"),
        api_key="test-key",
        timeout=10,
    )
    direct_get = MagicMock(side_effect=AssertionError("model listing must use NetriasClient"))
    monkeypatch.setattr("src.integrations.data_model_store.httpx.get", direct_get)

    # When
    summaries = list_data_model_summaries(mock_netrias)

    # Then: "gc" is first despite alphabetical ordering of raw data
    assert len(summaries) == 2, f"Expected 2 summaries, got {len(summaries)}"
    assert summaries[0].data_model_key == "gc"
    assert summaries[0].label == "Genomic Commons"
    assert [v.external_version_number for v in summaries[0].versions] == ["11.0.3", "11.0.4"]
    assert [asdict(v) for v in summaries[0].versions] == [
        {"external_version_number": "11.0.3"},
        {"external_version_number": "11.0.4"},
    ]
    assert summaries[1].data_model_key == "alpha"
    mock_netrias.list_data_models.assert_called_once_with(include_versions=True)
    direct_get.assert_not_called()


# ---------------------------------------------------------------------------
# Test: list_data_model_summaries returns [] when client is None
# ---------------------------------------------------------------------------


def test_list_summaries_returns_empty_when_client_unavailable() -> None:
    """
    Given: no NetriasClient (API key missing)
    When: list_data_model_summaries() is called
    Then: empty list returned (graceful degradation)
    """
    # When
    summaries = list_data_model_summaries(None)

    # Then
    assert summaries == [], f"Expected empty list, got {summaries}"


# ---------------------------------------------------------------------------
# Test: all-PV response parser groups PV sets by CDE key
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
    parsed = CdePvCatalog.empty()
    assert parsed.to_mapping() == {}

    # When
    parsed = _pv_map_from_all_pvs_response(response_body)

    # Then
    assert parsed.to_mapping() == {
        "diagnosis": frozenset({"Lung", "Breast"}),
        "sex": frozenset({"Female"}),
    }

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
    fetched = fetch_cdes(mock_netrias, "gc", "1")

    # Then: every CDE starts as PV (refinement happens later)
    assert {c.cde_type for c in fetched} == {CdeType.PV}


def test_fetch_cdes_passes_external_version_directly_without_model_lookup(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public external version is also the Data Model Store route identity."""

    mock_netrias.settings = SimpleNamespace(
        data_model_store_endpoints=SimpleNamespace(base_url="https://dms.example.test"),
        api_key="test-key",
        timeout=10,
    )
    mock_netrias.list_cdes.return_value = [
        SimpleNamespace(cde_id=1, cde_key="diagnosis", description="dx"),
    ]

    direct_get = MagicMock(side_effect=AssertionError("CDE lookup must not list data models"))
    monkeypatch.setattr("src.integrations.data_model_store.httpx.get", direct_get)

    fetched = fetch_cdes(mock_netrias, "gc", "11.0.4")

    assert [c.cde_key for c in fetched] == ["diagnosis"]
    mock_netrias.list_cdes.assert_called_once_with(
        model_key="gc",
        version="11.0.4",
        include_description=True,
    )
    mock_netrias.list_data_models.assert_not_called()
    direct_get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_all_pvs_places_external_version_directly_in_bulk_route(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulk PV lookup stays one request and uses the external version in its route."""

    mock_netrias.settings = SimpleNamespace(
        data_model_store_endpoints=SimpleNamespace(base_url="https://dms.example.test"),
        api_key="test-key",
        timeout=10,
    )
    requested_urls: list[str] = []

    class RecordingAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            assert timeout is not None

        async def __aenter__(self) -> RecordingAsyncClient:
            return self

        async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str],
            params: dict[str, str] | None = None,
        ) -> httpx.Response:
            assert headers == {"x-api-key": "test-key"}
            requested_urls.append(url)
            return httpx.Response(
                200,
                json={"items": [{"cde_key": "diagnosis", "pv_value": "Lung"}]},
                request=httpx.Request("GET", url, params=params),
            )

    monkeypatch.setattr("src.integrations.data_model_store.httpx.AsyncClient", RecordingAsyncClient)

    catalog = await fetch_all_pvs_async(mock_netrias, "gc", "11.0.4")

    assert requested_urls == ["https://dms.example.test/data-models/gc/versions/11.0.4/pvs"]
    assert catalog.get("diagnosis") == frozenset({"Lung"})


def test_fetch_cdes_preserves_unknown_external_version_error(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown external version remains an authoritative SDK boundary error."""

    mock_netrias.settings = SimpleNamespace(
        data_model_store_endpoints=SimpleNamespace(base_url="https://dms.example.test"),
        api_key="test-key",
        timeout=10,
    )
    expected = DataModelStoreError("unknown external version 99.0.0")
    mock_netrias.list_cdes.side_effect = expected
    direct_get = MagicMock(side_effect=AssertionError("CDE lookup must not list data models"))
    monkeypatch.setattr("src.integrations.data_model_store.httpx.get", direct_get)

    with pytest.raises(DataModelStoreError) as caught:
        fetch_cdes(mock_netrias, "gc", "99.0.0")

    assert caught.value is expected
    direct_get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_all_pvs_preserves_unknown_external_version_error(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct bulk route reports an unknown external version without fallback."""

    mock_netrias.settings = SimpleNamespace(
        data_model_store_endpoints=SimpleNamespace(base_url="https://dms.example.test"),
        api_key="test-key",
        timeout=10,
    )
    requested_urls: list[str] = []

    class NotFoundAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            assert timeout is not None

        async def __aenter__(self) -> NotFoundAsyncClient:
            return self

        async def __aexit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str],
            params: dict[str, str] | None = None,
        ) -> httpx.Response:
            assert headers == {"x-api-key": "test-key"}
            requested_urls.append(url)
            return httpx.Response(
                404,
                json={"detail": "unknown external version"},
                request=httpx.Request("GET", url, params=params),
            )

    monkeypatch.setattr("src.integrations.data_model_store.httpx.AsyncClient", NotFoundAsyncClient)

    with pytest.raises(DataModelStoreError, match="unknown external version"):
        await fetch_all_pvs_async(mock_netrias, "gc", "99.0.0")

    assert requested_urls == ["https://dms.example.test/data-models/gc/versions/99.0.0/pvs"]


def test_refine_cde_types_downgrades_to_passthrough_for_empty_pvs() -> None:
    """
    Given: two CDEs both initially typed as PV; PV fetch returned an empty
           frozenset for one of them
    When: refine_cde_types_from_pvs is called
    Then: the empty-PV CDE becomes PASSTHROUGH; the populated one stays PV
    """
    # Given
    cdes = [
        CDEInfo(cde_id=1, cde_key="diagnosis", description=None),
        CDEInfo(cde_id=2, cde_key="free_notes", description=None),
    ]
    pv_sets = CdePvCatalog.from_mapping({
        "diagnosis": frozenset({"A", "B"}),
        "free_notes": frozenset(),
    })
    # negative assertion: types start as PV
    assert all(c.cde_type == CdeType.PV for c in cdes)

    # When
    refined = refine_cde_types_from_pvs(CdeCatalog.from_cdes(cdes), pv_sets)

    # Then
    diagnosis = refined.get("diagnosis")
    free_notes = refined.get("free_notes")
    assert diagnosis is not None
    assert free_notes is not None
    assert diagnosis.cde_type == CdeType.PV
    assert free_notes.cde_type == CdeType.PASSTHROUGH


def test_refine_cde_types_skips_unfetched_cdes() -> None:
    """
    Given: two CDEs, but PVs were fetched for only one
    When: refine_cde_types_from_pvs is called
    Then: the un-fetched CDE keeps its original type unchanged
    """
    # Given
    cdes = [
        CDEInfo(cde_id=1, cde_key="diagnosis", description=None),
        CDEInfo(cde_id=2, cde_key="other", description=None),
    ]
    pv_sets = CdePvCatalog.from_mapping({"diagnosis": frozenset({"A"})})

    # When
    refined = refine_cde_types_from_pvs(CdeCatalog.from_cdes(cdes), pv_sets)

    # Then: untouched CDE remains as it was
    other = refined.get("other")
    assert other is not None
    assert other.cde_type == CdeType.PV


def test_refine_does_not_downgrade_when_fetch_failure_omits_key() -> None:
    """
    Given: a CDE that genuinely has PVs but its PV fetch failed and is therefore
           absent from the pv_sets dict
    When: refine_cde_types_from_pvs is called
    Then: the CDE stays at its initial type (PV) — it is NOT downgraded to
          PASSTHROUGH, which would happen if absent-key were treated the same
          as known-empty-set.
    """
    # Given
    cdes = [
        CDEInfo(cde_id=1, cde_key="primary_diagnosis", description=None),
    ]
    # PV sets dict is empty — the fetch failed for primary_diagnosis
    pv_sets = CdePvCatalog.empty()

    # When
    refined = refine_cde_types_from_pvs(CdeCatalog.from_cdes(cdes), pv_sets)

    # Then: type is PRESERVED at PV; no PASSTHROUGH downgrade
    primary_diagnosis = refined.get("primary_diagnosis")
    assert primary_diagnosis is not None
    assert primary_diagnosis.cde_type == CdeType.PV
