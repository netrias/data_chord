"""Requirement tests for Stage 4 review data and override persistence."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.domain.storage import UploadStorage
from tests.conftest import create_harmonized_csv, create_manifest_for_file, review_state_payload, upload_content
from tests.requirements.helpers import CANONICAL_DIAGNOSIS, DIAGNOSIS_COLUMN, ORIGINAL_DIAGNOSIS

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-027", "R-043", "R-044", "R-045", "R-048")
async def test_r027_r043_r044_r045_r048__review_rows_include_original_ai_metadata_and_persist_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A user has harmonized data ready for review.
    When: The user fetches Stage 4 rows and saves a manual review override.
    Then: Review data includes original and AI metadata, and the saved override can be loaded back.
    """
    # Given
    rows = [[DIAGNOSIS_COLUMN], [ORIGINAL_DIAGNOSIS]]
    file_id = await upload_content(app_client, f"{DIAGNOSIS_COLUMN}\n{ORIGINAL_DIAGNOSIS}\n".encode(), "review.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_DIAGNOSIS}})
    assert rows[1][0] == ORIGINAL_DIAGNOSIS

    # When
    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})

    # Then
    assert rows_response.status_code == 200
    transformation = rows_response.json()["columns"][0]["transformations"][0]
    assert transformation["originalValue"] == ORIGINAL_DIAGNOSIS
    assert transformation["harmonizedValue"] == CANONICAL_DIAGNOSIS
    assert transformation["confidence"] is not None
    assert transformation["topSuggestions"] == [{"value": CANONICAL_DIAGNOSIS, "isPVConformant": True}]
    assert transformation["rowIndices"] == [1]

    # When
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {
                    "0": {
                        "ai_value": CANONICAL_DIAGNOSIS,
                        "human_value": "Reviewed Melanoma",
                        "original_value": ORIGINAL_DIAGNOSIS,
                    }
                },
            },
            "review_state": review_state_payload(),
        },
    )
    loaded_response = await app_client.get(f"/stage-4/overrides/{file_id}")

    # Then
    assert save_response.status_code == 200
    assert loaded_response.status_code == 200
    loaded = loaded_response.json()
    assert loaded["overrides"]["1"]["0"]["human_value"] == "Reviewed Melanoma"
    assert loaded["review_state"]["review_mode"] == "column"
