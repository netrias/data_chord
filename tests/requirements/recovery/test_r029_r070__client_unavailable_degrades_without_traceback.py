"""Requirement tests for external client recovery and user-facing errors."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.domain.harmonize import HarmonizeService
from tests.conftest import TEST_TARGET_SCHEMA, upload_content
from tests.requirements.helpers import CANONICAL_DIAGNOSIS, DIAGNOSIS_COLUMN, single_column_csv

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-029", "R-070")
async def test_r029_r070__harmonize_client_unavailable_returns_controlled_response_without_traceback(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: The external harmonization client is unavailable.
    When: The user starts harmonization.
    Then: The API returns a controlled workflow response without exposing traceback details.
    """
    # Given
    file_id = await upload_content(
        app_client,
        single_column_csv(DIAGNOSIS_COLUMN, CANONICAL_DIAGNOSIS),
        "client-unavailable.csv",
    )
    monkeypatch.setattr(
        "src.stage_3_harmonize.router.get_harmonize_service",
        lambda: HarmonizeService(client=None),
    )
    assert file_id

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "manual_overrides": {}},
    )

    # Then
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert "unavailable" in body["detail"].lower()
    assert "traceback" not in response.text.lower()
