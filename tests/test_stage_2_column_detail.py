"""Tests for the Stage 2 column-detail use case and endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.manifest import ColumnMappingManifest
from src.domain.reference_data import ReferenceModel
from src.domain.workflow_state import WorkflowState
from src.persistence.workflow_artifacts import save_upload_artifacts
from src.persistence.workflow_state_store import save_initial_workflow_state
from src.stage_2_review_columns.router import _cde_catalog_item
from src.stage_2_review_columns.use_cases import (
    ColumnDetailNotFound,
    compute_column_detail,
)
from src.storage import LocalWorkflowStorage, UploadConstraints, UploadStorage, UserContext
from tests.conftest import TEST_XLSX_CONTENT_TYPE, create_xlsx_content

FILE_ID = dataset_workflow_id_from_string("abcdef0123456789abcdef0123456789")


class InMemoryUpload:
    """Small upload stream for creating durable test artifacts."""

    def __init__(
        self,
        content: bytes,
        filename: str = "dataset.csv",
        content_type: str = "text/csv",
    ) -> None:
        self.filename: str | None = filename
        self.content_type: str | None = content_type
        self._content = content
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        end = len(self._content) if size < 0 else self._offset + size
        chunk = self._content[self._offset:end]
        self._offset += len(chunk)
        return chunk

    async def close(self) -> None:
        return None


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
    reference_repository = MagicMock()
    reference_repository.load_model.return_value = ReferenceModel(
        version=DataModelVersionReference("gc", "11.0.4"),
        label="GC",
        catalog=CdeCatalog.from_cdes([
            CDEInfo(None, "dx", None, CdeType.PV),
            CDEInfo(None, "notes", None, CdeType.PASSTHROUGH),
        ]),
        pvs=CdePvCatalog.from_mapping({
            "dx": frozenset({"Lung", "Breast", "Glioma"}),
            "notes": frozenset(),
        }),
    )
    return await compute_column_detail(
        upload_storage=context.upload_storage,
        workflow_storage=context.workflow_storage,
        user=context.user,
        file_id=FILE_ID,
        column_key=column_key,
        selected_cde_key=selected_cde_key,
        reference_repository=reference_repository,
    )


async def _store_upload(
    context: StageTwoContext,
    content: bytes,
    *,
    filename: str = "dataset.csv",
    content_type: str = "text/csv",
) -> None:
    meta = await context.upload_storage.store(
        InMemoryUpload(content, filename, content_type),
        FILE_ID,
    )
    save_upload_artifacts(
        context.workflow_storage,
        context.user,
        context.upload_storage,
        meta,
    )


# ---------------------------------------------------------------------------
# Test: missing profile raises ColumnDetailNotFound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_raises_when_profile_missing(stage_two_context: StageTwoContext) -> None:
    """
    Given: a workflow with no durable upload for the requested column
    When: compute_column_detail is called
    Then: ColumnDetailNotFound is raised
    """
    # When / Then
    with pytest.raises(ColumnDetailNotFound):
        await _compute_detail(stage_two_context, "col", selected_cde_key=None)


# ---------------------------------------------------------------------------
# Test: PV-typed CDE returns sorted PVs and a positive match count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_returns_pv_match_and_sorted_pvs(
    stage_two_context: StageTwoContext,
) -> None:
    """
    Given: a column with values overlapping a PV-typed CDE's PV set
    When: compute_column_detail is called with that CDE selected
    Then: match_counts has the overlap; selected_pvs is the sorted PV list
    """
    # Given: the selected upload is durable, but no process-local profile is needed.
    await _store_upload(stage_two_context, b"col\nLung\nLung\nBreast\n")

    # When
    detail = await _compute_detail(stage_two_context, "col_0000", selected_cde_key="dx")

    # Then
    assert detail.column_key == "col_0000"
    assert detail.match_counts == {"dx": 2, "notes": 2}
    assert detail.cde_types == {"dx": "pv", "notes": "passthrough"}
    assert detail.overlap_by_cde == {"dx": 1.0}
    assert detail.selected_pvs == ["Breast", "Glioma", "Lung"]


# ---------------------------------------------------------------------------
# Test: empty PVs downgrade the CDE to PASSTHROUGH and selected_pvs is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_downgrades_to_passthrough_on_empty_pvs(
    stage_two_context: StageTwoContext,
) -> None:
    """
    Given: a CDE that returns an empty PV set (no PVs registered)
    When: compute_column_detail is called with that CDE selected
    Then: cde_types reports passthrough and selected_pvs is None
    """
    # Given: the selected upload contains values for a CDE with no permissible values.
    await _store_upload(stage_two_context, b"col\na\nb\n")

    # When
    detail = await _compute_detail(stage_two_context, "col_0000", selected_cde_key="notes")

    # Then: PASSTHROUGH counts everything
    assert detail.cde_types == {"dx": "pv", "notes": "passthrough"}
    assert detail.match_counts == {"notes": 2}
    assert detail.overlap_by_cde == {"dx": 0.0}
    assert detail.selected_pvs is None


# ---------------------------------------------------------------------------
# Test: reference data loads independently of the Stage 2 page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_loads_reference_data_for_durable_upload(
    stage_two_context: StageTwoContext,
) -> None:
    """
    Given: a durable upload exists but the Stage 2 page has not loaded CDE data
    When: compute_column_detail is called
    Then: complete reference data is returned from the repository
    """
    # Given
    await _store_upload(stage_two_context, b"col\nx\n")

    # When
    detail = await _compute_detail(stage_two_context, "col_0000", selected_cde_key=None)

    # Then
    assert detail.column_key == "col_0000"
    assert detail.match_counts == {"notes": 1}
    assert detail.cde_types == {"dx": "pv", "notes": "passthrough"}
    assert detail.overlap_by_cde == {"dx": 0.0}
    assert detail.selected_pvs is None


# ---------------------------------------------------------------------------
# Test: profile is rebuilt from the durable upload after a worker restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_column_detail_rebuilds_profile_from_durable_upload(
    stage_two_context: StageTwoContext,
    tmp_path: Path,
) -> None:
    """
    Given: the upload and workflow metadata survive in durable storage, but a
           new worker has an empty local upload directory
    When: compute_column_detail is called for a stored upload
    Then: the use case rebuilds that column's profile from the uploaded file and
          returns it with the detail payload
    """
    # Given: durable upload and workflow metadata survive a worker restart.
    await _store_upload(stage_two_context, b"diagnosis\nLung\nLung\nBreast\n")
    restarted_storage = UploadStorage(
        tmp_path / "restarted-worker-uploads",
        UploadConstraints(max_bytes=10_000),
    )
    restarted_context = StageTwoContext(
        upload_storage=restarted_storage,
        workflow_storage=stage_two_context.workflow_storage,
        user=stage_two_context.user,
    )

    # When
    detail = await _compute_detail(restarted_context, "col_0000", selected_cde_key="dx")

    # Then
    assert detail.profile is not None
    assert detail.profile.total_rows == 3
    assert [(dv.value, dv.count) for dv in detail.profile.distinct_values] == [
        ("Lung", 2),
        ("Breast", 1),
    ]


@pytest.mark.asyncio
async def test_compute_column_detail_uses_latest_durable_sheet_selection(
    stage_two_context: StageTwoContext,
) -> None:
    """A sheet change replaces the source of truth for column details."""
    workbook = create_xlsx_content({
        "First": [["diagnosis"], ["Lung"]],
        "Second": [["diagnosis"], ["Breast"], ["Breast"]],
    })
    await _store_upload(
        stage_two_context,
        workbook,
        filename="dataset.xlsx",
        content_type=TEST_XLSX_CONTENT_TYPE,
    )
    first_detail = await _compute_detail(stage_two_context, "col_0000", selected_cde_key="dx")
    assert first_detail.profile is not None
    assert [(value.value, value.count) for value in first_detail.profile.distinct_values] == [("Lung", 1)]

    save_initial_workflow_state(
        stage_two_context.workflow_storage,
        stage_two_context.user,
        WorkflowState.from_data_model_version(
            FILE_ID,
            DataModelVersionReference("gc", "11.0.4"),
            ColumnMappingManifest.empty(),
            selected_sheet="Second",
        ),
    )
    upload_meta = stage_two_context.upload_storage.load(FILE_ID)
    assert upload_meta is not None
    assert upload_meta.selected_sheet == "First"
    second_detail = await _compute_detail(stage_two_context, "col_0000", selected_cde_key="dx")

    assert second_detail.profile is not None
    assert [(value.value, value.count) for value in second_detail.profile.distinct_values] == [("Breast", 2)]
