"""Requirement tests for user-visible workflow navigation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-004")
async def test_r004__all_major_workflow_stage_pages_render(app_client: AsyncClient) -> None:
    """
    Given: The web application is running.
    When: The user opens each major workflow stage page.
    Then: Each stage page renders successfully.
    """
    # Given
    stage_paths = ["/stage-1", "/stage-2", "/stage-3", "/stage-4", "/stage-5"]
    assert len(stage_paths) == 5

    # When
    responses = [await app_client.get(path) for path in stage_paths]

    # Then
    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200]


@pytest.mark.requirements("R-077", "R-082", "R-084")
async def test_r077_r082_r084__stage_five_exposes_previous_stage_and_upload_navigation(
    app_client: AsyncClient,
) -> None:
    """
    Given: The user is on the final review stage.
    When: Stage 5 renders.
    Then: The page includes navigation targets for previous stages and returning to upload.
    """
    # Given
    expected_targets = ["/stage-1", "/stage-2", "/stage-3", "/stage-4"]
    assert "/stage-5" not in expected_targets

    # When
    response = await app_client.get("/stage-5")

    # Then
    assert response.status_code == 200
    for target in expected_targets:
        assert target in response.text
