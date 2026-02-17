"""Consolidated error handling tests across all endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from netrias_client import DataModelStoreError

from tests.conftest import TEST_CSV_CONTENT_TYPE, TEST_TARGET_SCHEMA, upload_file

pytestmark = pytest.mark.asyncio

INVALID_FILE_ID = "deadbeef12345678deadbeef12345678"


class TestMissingFileErrors:
    """All endpoints return 404 for non-existent file_id."""

    async def test_analyze_missing_file(self, app_client: AsyncClient) -> None:
        """Analyze returns 404 for unknown file_id."""

        # Given: A file_id that does not exist in storage

        # When: Analyze is called with the non-existent file_id
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": INVALID_FILE_ID, "target_schema": TEST_TARGET_SCHEMA},
        )

        # Then: 404 response with "not found" message
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_harmonize_missing_file(self, app_client: AsyncClient) -> None:
        """Harmonize returns 404 for unknown file_id."""

        # Given: A file_id that does not exist in storage

        # When: Harmonize is called with the non-existent file_id
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": INVALID_FILE_ID,
                "target_schema": TEST_TARGET_SCHEMA,
                "manual_overrides": {},
            },
        )

        # Then: 404 response
        assert response.status_code == 404

    async def test_rows_missing_file(self, app_client: AsyncClient) -> None:
        """Rows returns 404 for unknown file_id."""

        # Given: A file_id that does not exist in storage (valid hex format)

        # When: Rows are requested with the non-existent file_id
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": INVALID_FILE_ID, "manual_columns": []},
        )

        # Then: 404 response
        assert response.status_code == 404

    async def test_summary_missing_file(self, app_client: AsyncClient) -> None:
        """Summary returns 404 for unknown file_id."""

        # Given: A file_id that does not exist in storage (valid hex format)

        # When: Summary is requested with the non-existent file_id
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": INVALID_FILE_ID, "manual_columns": []},
        )

        # Then: 404 response
        assert response.status_code == 404


class TestMissingHarmonizedFileErrors:
    """Stage 4 and 5 return 404 when harmonized file doesn't exist."""

    async def test_rows_missing_harmonized(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Rows returns 404 when harmonization manifest doesn't exist."""

        # Given: An uploaded file without harmonized output
        file_id = await upload_file(app_client, sample_csv_path)

        # When: Rows are requested before harmonization
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: 404 response indicating manifest not found
        assert response.status_code == 404
        assert "manifest" in response.json()["detail"].lower()

    async def test_summary_missing_harmonized(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Summary returns 404 when harmonized CSV doesn't exist."""

        # Given: An uploaded file without harmonized output
        file_id = await upload_file(app_client, sample_csv_path)

        # When: Summary is requested before harmonization
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: 404 response
        assert response.status_code == 404


class TestDataModelServiceErrors:
    """Data model endpoint error handling."""

    async def test_list_data_models_returns_503_when_api_unavailable(
        self, app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """503 returned when Data Model Store API is unreachable."""
        # Given: Data Model Store API is unreachable
        monkeypatch.setattr(
            "src.stage_1_upload.router.list_data_model_summaries",
            MagicMock(side_effect=DataModelStoreError("Connection failed")),
        )

        # When: GET /stage-1/data-models is called
        response = await app_client.get("/stage-1/data-models")

        # Then: 503 response with user-friendly message
        assert response.status_code == 503
        assert "currently unavailable" in response.json()["detail"]


class TestUploadValidationErrors:
    """Upload endpoint validates file type and size."""

    async def test_unsupported_extension_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files with unsupported extensions."""

        # Given: A file with .xlsx extension (not supported)

        # When: The file is uploaded
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

        # Then: 415 Unsupported Media Type response
        assert response.status_code == 415
        assert "unsupported" in response.json()["detail"].lower()

    async def test_unsupported_content_type_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files with unsupported content types."""

        # Given: A file with JSON content type (not supported)

        # When: The file is uploaded
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("test.json", b'{"data": "test"}', "application/json")},
        )

        # Then: 415 Unsupported Media Type response
        assert response.status_code == 415

    async def test_oversized_file_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files exceeding size limit."""

        # Given: A file exceeding the 25MB size limit
        oversized_content = b"x" * (26 * 1024 * 1024)

        # When: The oversized file is uploaded
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("large.csv", oversized_content, TEST_CSV_CONTENT_TYPE)},
        )

        # Then: 413 Payload Too Large response
        assert response.status_code == 413
        assert "exceeds" in response.json()["detail"].lower()
