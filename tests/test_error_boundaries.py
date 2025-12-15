"""Consolidated error handling tests across all endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_CSV_CONTENT_TYPE, TEST_TARGET_SCHEMA, upload_file

pytestmark = pytest.mark.asyncio

INVALID_FILE_ID = "deadbeef12345678deadbeef12345678"


class TestMissingFileErrors:
    """All endpoints return 404 for non-existent file_id."""

    async def test_analyze_missing_file(self, app_client: AsyncClient) -> None:
        """Analyze returns 404 for unknown file_id."""
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": INVALID_FILE_ID, "target_schema": TEST_TARGET_SCHEMA},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_harmonize_missing_file(self, app_client: AsyncClient) -> None:
        """Harmonize returns 404 for unknown file_id."""
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": INVALID_FILE_ID,
                "target_schema": TEST_TARGET_SCHEMA,
                "manual_overrides": {},
            },
        )
        assert response.status_code == 404

    async def test_rows_missing_file(self, app_client: AsyncClient) -> None:
        """Rows returns 404 for unknown file_id."""
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": "nonexistent123", "manual_columns": []},
        )
        assert response.status_code == 404

    async def test_summary_missing_file(self, app_client: AsyncClient) -> None:
        """Summary returns 404 for unknown file_id."""
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": "nonexistent123", "manual_columns": []},
        )
        assert response.status_code == 404


class TestMissingHarmonizedFileErrors:
    """Stage 4 and 5 return 404 when harmonized file doesn't exist."""

    async def test_rows_missing_harmonized(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Rows returns 404 when harmonization manifest doesn't exist."""
        file_id = await upload_file(app_client, sample_csv_path)

        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        assert response.status_code == 404
        assert "manifest" in response.json()["detail"].lower()

    async def test_summary_missing_harmonized(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Summary returns 404 when harmonized CSV doesn't exist."""
        file_id = await upload_file(app_client, sample_csv_path)

        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id, "manual_columns": []},
        )

        assert response.status_code == 404


class TestUploadValidationErrors:
    """Upload endpoint validates file type and size."""

    async def test_unsupported_extension_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files with unsupported extensions."""
        response = await app_client.post(
            "/stage-1/upload",
            files={
                "file": (
                    "test.xlsx",
                    b"fake xlsx content",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 415
        assert "unsupported" in response.json()["detail"].lower()

    async def test_unsupported_content_type_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files with unsupported content types."""
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("test.json", b'{"data": "test"}', "application/json")},
        )
        assert response.status_code == 415

    async def test_oversized_file_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files exceeding size limit."""
        oversized_content = b"x" * (26 * 1024 * 1024)

        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("large.csv", oversized_content, TEST_CSV_CONTENT_TYPE)},
        )

        assert response.status_code == 413
        assert "exceeds" in response.json()["detail"].lower()
