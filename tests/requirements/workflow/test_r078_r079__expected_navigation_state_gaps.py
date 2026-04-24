"""Expected-failing requirement tests for workflow navigation state gaps."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_TARGET_SCHEMA, upload_content
from tests.requirements.helpers import CANONICAL_DIAGNOSIS, DIAGNOSIS_COLUMN, single_column_csv

pytestmark = pytest.mark.asyncio


@pytest.mark.xfail(
    reason="Stage 2 currently relies on browser sessionStorage instead of server-rendered saved state.",
    strict=True,
)
@pytest.mark.requirements("R-078")
async def test_r078__back_to_stage_two_renders_saved_mapping_state(app_client: AsyncClient) -> None:
    """
    Given: A user has uploaded and analyzed a file.
    When: The user navigates back to Stage 2 for that workflow.
    Then: Stage 2 renders the saved mapping state for the current workflow.
    """
    # Given
    file_id = await upload_content(app_client, single_column_csv(DIAGNOSIS_COLUMN, CANONICAL_DIAGNOSIS), "mapped.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    assert analyze_response.status_code == 200

    # When
    response = await app_client.get(f"/stage-2?file_id={file_id}&schema={TEST_TARGET_SCHEMA}")

    # Then
    assert response.status_code == 200
    assert DIAGNOSIS_COLUMN in response.text
    assert "selected" in response.text


@pytest.mark.xfail(
    reason="Forward controls do not yet expose a machine-readable action-vs-navigation distinction.",
    strict=True,
)
@pytest.mark.requirements("R-079")
async def test_r079__forward_controls_distinguish_navigation_from_workflow_actions(app_client: AsyncClient) -> None:
    """
    Given: A user is viewing workflow stage pages.
    When: The pages render forward navigation or action controls.
    Then: Controls expose whether they only navigate or run a workflow action.
    """
    # Given
    stage_paths = ["/stage-1", "/stage-2", "/stage-3", "/stage-4", "/stage-5"]
    assert stage_paths

    # When
    responses = [await app_client.get(path) for path in stage_paths]

    # Then
    for response in responses:
        assert response.status_code == 200
        assert 'data-forward-behavior="' in response.text
