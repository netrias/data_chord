"""Tests for the Stage 2 column-detail use case and endpoint."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
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
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.manifest import ColumnMappingManifest
from src.domain.workflow_state import WorkflowState
from src.persistence.workflow_artifacts import save_upload_artifacts
from src.persistence.workflow_state_store import save_initial_workflow_state
from src.stage_2_review_columns.router import _cde_catalog_item
from src.stage_2_review_columns.use_cases import (
    ColumnDetailNotFound,
    compute_column_detail,
)
from src.storage import LocalWorkflowStorage, UploadConstraints, UploadStorage, UserContext

FILE_ID = dataset_workflow_id_from_string("abcdef0123456789abcdef0123456789")


def test_cde_catalog_item_contains_only_browser_fields() -> None:
    cde = CDEInfo(cde_id=7, cde_key="diagnosis", description="Diagnosis")

    assert _cde_catalog_item(cde) == {
        "cde_key": "diagnosis",
        "description": "Diagnosis",
        "cde_type": "pv",
    }


@dataclass(frozen=True)
class StageTwoContext:
    upload_storage: UploadStorage
    workflow_storage: LocalWorkflowStorage
    user: UserContext


@pytest.fixture
def stage_two_context(tmp_path: Path) -> StageTwoContext:
    upload_storage = UploadStorage(tmp_path / "uploads", UploadConstraints(max_bytes=10_000))
    workflow_storage = LocalWorkflowStorage(tmp_path / "workflows")
    user = UserContext(user_id="local-user")
    workflow_storage.create_workflow(user, FILE_ID)
    save_initial_workflow_state(
        workflow_storage,
        user,
        WorkflowState.from_data_model_version(
            FILE_ID,
            DataModelVersionReference("gc", "11.0.4"),
            ColumnMappingManifest.empty(),
        ),
    )
    return StageTwoContext(upload_storage, workflow_storage, user)


async def _compute_detail(
    context: StageTwoContext,
    column_key: str,
    selected_cde_key: str | None,
):
    return await compute_column_detail(
        upload_storage=context.upload_storage,
        workflow_storage=context.workflow_storage,
        user=context.user,
        file_id=FILE_ID,
        column_key=column_key,
        selected_cde_key=selected_cde_key,
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
async def test_compute_column_detail_raises_when_profile_missing(stage_two_context: StageTwoContext) -> None:
    """
    Given: a session cache with no profile for the requested column
    When: compute_column_detail is called
    Then: ColumnDetailNotFound is raised
    """
    # Given: cache empty (negative assertion)
    cache = get_session_cache(FILE_ID)
    assert cache.get_column_profile("col") is None

    # When / Then
    with pytest.raises(ColumnDetailNotFound):
        await _compute_detail(stage_two_context, "col", selected_cde_key=None)


# ---------------------------------------------------------------------------
# Test: PV-typed CDE returns sorted PVs and a positive match count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_returns_pv_match_and_sorted_pvs(
    stage_two_context: StageTwoContext,
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a column with values overlapping a PV-typed CDE's PV set
    When: compute_column_detail is called with that CDE selected
    Then: match_counts has the overlap; selected_pvs is the sorted PV list
    """
    # Given
    file_id = FILE_ID
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
    detail = await _compute_detail(stage_two_context, "col", selected_cde_key="dx")

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
    stage_two_context: StageTwoContext,
    mock_netrias: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: a CDE that returns an empty PV set (no PVs registered)
    When: compute_column_detail is called with that CDE selected
    Then: cde_types reports passthrough and selected_pvs is None
    """
    # Given
    file_id = FILE_ID
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
    detail = await _compute_detail(stage_two_context, "col", selected_cde_key="notes")

    # Then: PASSTHROUGH counts everything
    assert detail.cde_types == {"notes": "passthrough"}
    assert detail.match_counts == {"notes": 2}
    assert detail.overlap_by_cde == {}
    assert detail.selected_pvs is None


# ---------------------------------------------------------------------------
# Test: empty CDE catalog returns an empty payload (the page hasn't loaded yet)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_returns_empty_when_cdes_not_yet_loaded(
    stage_two_context: StageTwoContext,
) -> None:
    """
    Given: a profile is cached but CDEs haven't been populated by the Stage 2 page
    When: compute_column_detail is called
    Then: response is empty rather than raising — frontend can retry
    """
    # Given
    file_id = FILE_ID
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
    detail = await _compute_detail(stage_two_context, "col", selected_cde_key=None)

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
    stage_two_context: StageTwoContext,
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
    # Given
    storage = stage_two_context.upload_storage
    csv_path = storage._data_dir / f"{FILE_ID}.csv"
    csv_path.write_text("diagnosis\nLung\nLung\nBreast\n", encoding="utf-8")
    meta_path = storage._meta_dir / f"{FILE_ID}.json"
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
    meta = storage.load(FILE_ID)
    assert meta is not None
    save_upload_artifacts(
        stage_two_context.workflow_storage,
        stage_two_context.user,
        storage,
        meta,
    )

    file_id = FILE_ID
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
    detail = await _compute_detail(stage_two_context, "col_0000", selected_cde_key="dx")

    # Then
    assert detail.profile is not None
    assert detail.profile.total_rows == 3
    assert [(dv.value, dv.count) for dv in detail.profile.distinct_values] == [
        ("Lung", 2),
        ("Breast", 1),
    ]
    assert cache.get_column_profile("col_0000") is not None
