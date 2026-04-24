"""Requirement tests for column mapping decisions at the harmonize boundary."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from src.domain.cde import NO_MAPPING_SENTINEL
from src.domain.data_model_cache import get_session_cache
from tests.conftest import TEST_TARGET_SCHEMA, upload_content
from tests.requirements.helpers import (
    AGE_AT_DIAGNOSIS_CDE,
    AGE_COLUMN,
    CANONICAL_DIAGNOSIS,
    DIAGNOSIS_COLUMN,
    NOTES_COLUMN,
    PRIMARY_DIAGNOSIS_CDE,
    load_reference_json,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements(
    "R-015", "R-016", "R-017", "R-018", "R-019", "R-021", "R-022", "R-023", "R-025", "R-026"
)
async def test_r015_r016_r017_r018_r019_r021_r022_r023_r025_r026__harmonize_resolves_column_assignments_by_position(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
) -> None:
    """
    Given: A user has AI mappings, a manual override, a numeric pass-through, and an unmapped column.
    When: The user starts harmonization from Stage 3.
    Then: Harmonization uses resolved positional assignments and sends only harmonizable mappings to the SDK.
    """
    # Given
    content = f"{DIAGNOSIS_COLUMN},{AGE_COLUMN},{NOTES_COLUMN}\n{CANONICAL_DIAGNOSIS},42,free text\n".encode()
    file_id = await upload_content(app_client, content, "assignments.csv")
    stage_two = await app_client.get(f"/stage-2?file_id={file_id}&schema={TEST_TARGET_SCHEMA}")
    assert stage_two.status_code == 200

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {"0": PRIMARY_DIAGNOSIS_CDE, "2": NO_MAPPING_SENTINEL},
            "manifest": load_reference_json("stage3_assignment_resolution_manifest.json"),
        },
    )

    # Then
    assert response.status_code == 200
    assignments = get_session_cache(file_id).get_column_assignments()
    assert assignments[0].column_id == 0
    assert assignments[0].column_name == DIAGNOSIS_COLUMN
    assert assignments[0].cde_key == PRIMARY_DIAGNOSIS_CDE
    assert assignments[0].harmonization == "harmonizable"
    assert assignments[1].cde_key == AGE_AT_DIAGNOSIS_CDE
    assert assignments[1].harmonization == "numeric"
    assert assignments[2].cde_key is None
    assert assignments[2].harmonization is None

    harmonize_manifest = mock_netrias_client.harmonize.call_args.kwargs["manifest"]
    assert harmonize_manifest["column_mappings"][0]["cde_key"] == PRIMARY_DIAGNOSIS_CDE
    assert harmonize_manifest["column_mappings"][1] is None
    assert harmonize_manifest["column_mappings"][2] is None
