"""Requirement tests for recoverable user-facing API errors."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.requirements.helpers import CANONICAL_DIAGNOSIS, CSV_MIME_TYPE, DIAGNOSIS_COLUMN, single_column_csv

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-006", "R-069")
async def test_r006_r069__unsupported_upload_returns_actionable_user_error(app_client: AsyncClient) -> None:
    """
    Given: A user selects a file type that the application does not support.
    When: The user uploads that file through Stage 1.
    Then: The API returns a clear user-facing error message.
    """
    # Given
    filename = "not-csv.json"
    assert not filename.endswith(".csv")

    # When
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": (filename, b'{"not": "csv"}', "application/json")},
    )

    # Then
    assert response.status_code == 415
    assert "unsupported" in response.json()["detail"].lower()


@pytest.mark.requirements("R-006", "R-068", "R-069")
async def test_r006_r068_r069__missing_manifest_returns_recoverable_stage_error(
    app_client: AsyncClient,
) -> None:
    """
    Given: A user has uploaded a file but has not completed harmonization.
    When: The user requests Stage 4 review rows.
    Then: The API returns a recoverable error explaining that the manifest is missing.
    """
    # Given
    upload = await app_client.post(
        "/stage-1/upload",
        files={
            "file": (
                "source.csv",
                single_column_csv(DIAGNOSIS_COLUMN, CANONICAL_DIAGNOSIS),
                CSV_MIME_TYPE,
            )
        },
    )
    file_id = upload.json()["file_id"]
    assert upload.status_code == 201

    # When
    response = await app_client.post("/stage-4/rows", json={"file_id": file_id})

    # Then
    assert response.status_code == 404
    detail = response.json()["detail"].lower()
    assert "manifest" in detail
    assert "rerun" in detail
