"""Expected-failing requirement tests for stale downstream workflow state."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.domain.storage import UploadStorage
from tests.conftest import TEST_TARGET_SCHEMA, create_harmonized_csv, create_manifest_for_file, upload_content
from tests.requirements.helpers import CANONICAL_DIAGNOSIS, DIAGNOSIS_COLUMN, ORIGINAL_DIAGNOSIS

pytestmark = pytest.mark.asyncio


@pytest.mark.xfail(reason="No stale-downstream-state model exists yet after upstream mapping changes.", strict=True)
@pytest.mark.requirements("R-080", "R-081")
async def test_r080_r081__changed_stage_two_mapping_blocks_stale_review_until_reharmonized(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A user has completed harmonization and then changes the upstream column mapping.
    When: The user tries to review results without rerunning harmonization.
    Then: The application blocks stale review results and requires harmonization to run again.
    """
    # Given
    file_id = await upload_content(app_client, f"{DIAGNOSIS_COLUMN}\n{ORIGINAL_DIAGNOSIS}\n".encode(), "stale.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    assert (await app_client.get(f"/stage-2?file_id={file_id}&schema={TEST_TARGET_SCHEMA}")).status_code == 200

    # When
    response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "upstream_mapping_revision": "changed-after-harmonize"},
    )

    # Then
    assert response.status_code == 409
    assert "rerun" in response.json()["detail"].lower()
