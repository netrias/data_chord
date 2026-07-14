"""Tests for the Stage 2 column-detail use case and endpoint."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.session_cache import (
    clear_all_session_caches,
    get_session_cache,
)
from src.domain.cde import CDEInfo
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_profile import ColumnProfile, DistinctValue
from src.stage_2_review_columns.use_cases import (
    ColumnDetailNotFound,
    compute_column_detail,
)


@pytest.fixture(autouse=True)
def _isolate_session_cache() -> Generator[None]:
    clear_all_session_caches()
    yield
    clear_all_session_caches()


@pytest.fixture
def mock_netrias() -> Generator[MagicMock]:
    """Inject a MagicMock NetriasClient that returns deterministic PVs."""
    import src.app.dependencies as deps

    saved = deps._netrias_client, deps._netrias_client_initialized
    mock = MagicMock()
    deps._netrias_client = mock
    deps._netrias_client_initialized = True
    yield mock
    deps._netrias_client, deps._netrias_client_initialized = saved


# ---------------------------------------------------------------------------
# Test: missing profile raises ColumnDetailNotFound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_raises_when_profile_missing() -> None:
    """
    Given: a session cache with no profile for the requested column
    When: compute_column_detail is called
    Then: ColumnDetailNotFound is raised
    """
    # Given: cache empty (negative assertion)
    cache = get_session_cache("abcdef0123456789abcdef0123456789")
    assert cache.get_column_profile("col") is None

    # When / Then
    with pytest.raises(ColumnDetailNotFound):
        await compute_column_detail("abcdef0123456789abcdef0123456789", "col", selected_cde_key=None)


# ---------------------------------------------------------------------------
# Test: PV-typed CDE returns sorted PVs and a positive match count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_returns_pv_match_and_sorted_pvs(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a column with values overlapping a PV-typed CDE's PV set
    When: compute_column_detail is called with that CDE selected
    Then: match_counts has the overlap; selected_pvs is the sorted PV list
    """
    # Given
    file_id = "abcdef0123456789abcdef0123456789"
    cache = get_session_cache(file_id)
    cache.set_column_profiles({
        "col": ColumnProfile(
            column_key="col",
            total_rows=3,
            distinct_values=(
                DistinctValue("Lung", 2),
                DistinctValue("Breast", 1),
            ),
            null_count=0,
        )
    })
    cache.set_cdes(
        [CDEInfo(cde_id=1, cde_key="dx", description=None)],
        data_model_key="gc",
        external_version_number="11.0.4",
    )

    monkeypatch.setattr(
        "src.stage_2_review_columns.use_cases.fetch_all_pvs_async",
        AsyncMock(return_value=CdePvCatalog.from_mapping({"dx": frozenset({"Lung", "Breast", "Glioma"})})),
    )

    # When
    detail = await compute_column_detail(file_id, "col", selected_cde_key="dx")

    # Then
    assert detail.column_key == "col"
    assert detail.match_counts == {"dx": 2}
    assert detail.cde_types == {"dx": "pv"}
    assert detail.overlap_by_cde == {"dx": 1.0}
    assert detail.selected_pvs == ["Breast", "Glioma", "Lung"]


# ---------------------------------------------------------------------------
# Test: empty PVs downgrade the CDE to PASSTHROUGH and selected_pvs is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_downgrades_to_passthrough_on_empty_pvs(
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a CDE that returns an empty PV set (no PVs registered)
    When: compute_column_detail is called with that CDE selected
    Then: cde_types reports passthrough and selected_pvs is None
    """
    # Given
    file_id = "abcdef0123456789abcdef0123456789"
    cache = get_session_cache(file_id)
    cache.set_column_profiles({
        "col": ColumnProfile(
            column_key="col",
            total_rows=2,
            distinct_values=(DistinctValue("a", 1), DistinctValue("b", 1)),
            null_count=0,
        )
    })
    cache.set_cdes(
        [CDEInfo(cde_id=2, cde_key="notes", description=None)],
        data_model_key="gc",
        external_version_number="11.0.4",
    )
    monkeypatch.setattr(
        "src.stage_2_review_columns.use_cases.fetch_all_pvs_async",
        AsyncMock(return_value=CdePvCatalog.from_mapping({"notes": frozenset()})),
    )

    # When
    detail = await compute_column_detail(file_id, "col", selected_cde_key="notes")

    # Then: PASSTHROUGH counts everything
    assert detail.cde_types == {"notes": "passthrough"}
    assert detail.match_counts == {"notes": 2}
    assert detail.overlap_by_cde == {}
    assert detail.selected_pvs is None


# ---------------------------------------------------------------------------
# Test: empty CDE catalog returns an empty payload (the page hasn't loaded yet)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_returns_empty_when_cdes_not_yet_loaded() -> None:
    """
    Given: a profile is cached but CDEs haven't been populated by the Stage 2 page
    When: compute_column_detail is called
    Then: response is empty rather than raising — frontend can retry
    """
    # Given
    file_id = "abcdef0123456789abcdef0123456789"
    cache = get_session_cache(file_id)
    cache.set_column_profiles({
        "col": ColumnProfile(
            column_key="col",
            total_rows=1,
            distinct_values=(DistinctValue("x", 1),),
            null_count=0,
        )
    })
    assert not cache.has_cdes()

    # When
    detail = await compute_column_detail(file_id, "col", selected_cde_key=None)

    # Then
    assert detail.column_key == "col"
    assert detail.match_counts == {}
    assert detail.cde_types == {}
    assert detail.overlap_by_cde == {}
    assert detail.selected_pvs is None


# ---------------------------------------------------------------------------
# Test: missing in-memory profile is rebuilt from the uploaded file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_rebuilds_profile_when_cache_lost(
    tmp_path: Path,
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: browser/session state survived but the server's in-memory column
           profile cache was cleared
    When: compute_column_detail is called for a stored upload
    Then: the use case rebuilds that column's profile from the uploaded file and
          returns it with the detail payload
    """
    import src.stage_2_review_columns.use_cases as use_cases
    from src.storage import UploadConstraints, UploadStorage

    # Given
    storage = UploadStorage(tmp_path / "uploads", UploadConstraints(max_bytes=10_000))
    csv_path = storage._data_dir / "abcdef0123456789abcdef0123456789.csv"
    csv_path.write_text("diagnosis\nLung\nLung\nBreast\n", encoding="utf-8")
    meta_path = storage._meta_dir / "abcdef0123456789abcdef0123456789.json"
    meta_path.write_text(
        """
        {
          "file_id": "abcdef0123456789abcdef0123456789",
          "original_name": "diagnosis.csv",
          "content_type": "text/csv",
          "size_bytes": 27,
          "saved_name": "abcdef0123456789abcdef0123456789.csv",
          "uploaded_at": "2026-04-29T00:00:00+00:00",
          "tabular_format": "csv",
          "sheet_names": [],
          "selected_sheet": null
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(use_cases.dependencies, "get_upload_storage", lambda: storage)

    file_id = "abcdef0123456789abcdef0123456789"
    cache = get_session_cache(file_id)
    cache.set_cdes(
        [CDEInfo(cde_id=1, cde_key="dx", description=None)],
        data_model_key="gc",
        external_version_number="11.0.4",
    )
    assert cache.get_column_profile("col_0000") is None
    monkeypatch.setattr(
        "src.stage_2_review_columns.use_cases.fetch_all_pvs_async",
        AsyncMock(return_value=CdePvCatalog.from_mapping({"dx": frozenset({"Lung", "Breast"})})),
    )

    # When
    detail = await compute_column_detail(file_id, "col_0000", selected_cde_key="dx")

    # Then
    assert detail.profile is not None
    assert detail.profile.total_rows == 3
    assert [(dv.value, dv.count) for dv in detail.profile.distinct_values] == [
        ("Lung", 2),
        ("Breast", 1),
    ]
    assert cache.get_column_profile("col_0000") is not None
