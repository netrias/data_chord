"""Requirement tests for PV lookup and recovery through review endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.domain.column_assignment import ColumnAssignment
from src.domain.data_model_cache import clear_all_session_caches, get_session_cache
from src.domain.pv_persistence import save_pv_manifest_to_disk
from src.domain.storage import UploadStorage
from tests.conftest import create_harmonized_csv, create_manifest_for_file, upload_content
from tests.requirements.helpers import CANONICAL_DIAGNOSIS, DIAGNOSIS_COLUMN, ORIGINAL_DIAGNOSIS, PRIMARY_DIAGNOSIS_CDE

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-037", "R-039")
async def test_r037_r039__stage_four_recovers_pvs_by_column_identity_after_cache_clear(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: PV data was persisted for a harmonized workflow and in-memory cache was cleared.
    When: The user opens Stage 4 review rows.
    Then: PVs are recovered by column identity and exposed for the review column.
    """
    # Given
    file_id = await upload_content(app_client, f"{DIAGNOSIS_COLUMN}\n{ORIGINAL_DIAGNOSIS}\n".encode(), "pv.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    cache = get_session_cache(file_id)
    cache.set_column_assignments({0: ColumnAssignment(0, DIAGNOSIS_COLUMN, PRIMARY_DIAGNOSIS_CDE, "harmonizable")})
    save_pv_manifest_to_disk(file_id, cache, {PRIMARY_DIAGNOSIS_CDE: frozenset([CANONICAL_DIAGNOSIS])})
    clear_all_session_caches()
    assert not get_session_cache(file_id).has_any_pvs()

    # When
    response = await app_client.post("/stage-4/rows", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    assert response.json()["columnPVs"] == {"0": [CANONICAL_DIAGNOSIS]}


@pytest.mark.requirements("R-040")
async def test_r040__stage_four_continues_when_pv_manifest_is_missing(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A workflow has reviewable harmonization data but no persisted PV manifest.
    When: The user opens Stage 4 review rows.
    Then: Review data loads without crashing and PV-dependent data is absent.
    """
    # Given
    file_id = await upload_content(app_client, f"{DIAGNOSIS_COLUMN}\n{ORIGINAL_DIAGNOSIS}\n".encode(), "no-pv.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    clear_all_session_caches()
    assert not get_session_cache(file_id).has_any_pvs()

    # When
    response = await app_client.post("/stage-4/rows", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    assert response.json()["columnPVs"] == {}
