"""Requirement tests for recoverable missing-upload errors."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_TARGET_SCHEMA

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-067")
async def test_r067__missing_upload_returns_clear_not_found_response(app_client: AsyncClient) -> None:
    """
    Given: A workflow request references a file id that is not in upload storage.
    When: The user tries to harmonize that missing file.
    Then: The API returns a not-found response instead of an unhandled exception.
    """
    # Given
    missing_file_id = "deadbeef12345678deadbeef12345678"
    assert missing_file_id

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": missing_file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
        },
    )

    # Then
    assert response.status_code == 404
    assert response.json()["detail"]
