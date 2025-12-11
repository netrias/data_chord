"""Contract tests validating HTTP API request/response shapes per endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from src.stage_1_upload.services import UploadStorage
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_TARGET_SCHEMA,
    create_harmonized_csv,
    upload_file,
)

pytestmark = pytest.mark.asyncio


class TestUploadContract:
    """POST /stage-1/upload accepts CSVs and returns UploadResponse."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Upload response includes all UploadResponse schema fields."""

        # When
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
        )

        # Then
        assert response.status_code == 201
        data = response.json()
        assert "file_id" in data
        assert "file_name" in data
        assert "human_size" in data
        assert "content_type" in data
        assert "uploaded_at" in data

    async def test_file_id_is_hex_string(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """file_id is a valid hex string for use in subsequent requests."""

        # When
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
        )

        # Then
        file_id = response.json()["file_id"]
        assert len(file_id) >= 8
        assert all(c in "0123456789abcdef" for c in file_id)

    @pytest.mark.parametrize(
        ("filename", "content_type", "expected_status"),
        [
            ("test.csv", "text/csv", 201),
            ("test.csv", "application/csv", 201),
            ("test.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 415),
            ("test.json", "application/json", 415),
        ],
    )
    async def test_content_type_validation(
        self,
        app_client: AsyncClient,
        filename: str,
        content_type: str,
        expected_status: int,
    ) -> None:
        """Only CSV content types are accepted."""

        # When
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": (filename, b"col1,col2\na,b", content_type)},
        )

        # Then
        assert response.status_code == expected_status


class TestAnalyzeContract:
    """POST /stage-1/analyze returns column metadata and CDE suggestions."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Analyze response includes all AnalyzeResponse schema fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)

        # When
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        # Then
        assert response.status_code == 200
        data = response.json()
        assert data["file_id"] == file_id
        assert "file_name" in data
        assert "total_rows" in data
        assert "columns" in data
        assert "cde_targets" in data
        assert "next_stage" in data
        assert "manifest" in data

    async def test_columns_have_required_fields(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Each column in response has ColumnPreview fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)

        # When
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        # Then
        columns = response.json()["columns"]
        assert len(columns) > 0
        for col in columns:
            assert "column_name" in col
            assert "inferred_type" in col
            assert "sample_values" in col
            assert "confidence_bucket" in col
            assert col["confidence_bucket"] in ("low", "medium", "high")

    @pytest.mark.parametrize(
        ("column_name", "expected_type"),
        [
            ("numeric_col", "numeric"),
            ("date_col", "date"),
            ("text_col", "text"),
        ],
    )
    async def test_type_inference(
        self,
        app_client: AsyncClient,
        types_csv_path: Path,
        column_name: str,
        expected_type: str,
    ) -> None:
        """Columns are detected with correct inferred types."""

        # Given
        file_id = await upload_file(app_client, types_csv_path)

        # When
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        # Then
        data = response.json()
        col = next(col for col in data["columns"] if col["column_name"] == column_name)
        assert col["inferred_type"] == expected_type

    @pytest.mark.parametrize(
        ("column_name", "expected_bucket"),
        [
            ("high_confidence", "high"),
            ("medium_confidence", "medium"),
            ("low_confidence", "low"),
        ],
    )
    async def test_confidence_bucket_by_null_ratio(
        self,
        app_client: AsyncClient,
        with_nulls_csv_path: Path,
        column_name: str,
        expected_bucket: str,
    ) -> None:
        """Confidence bucket assigned based on non-null ratio."""

        # Given
        file_id = await upload_file(app_client, with_nulls_csv_path)

        # When
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        # Then
        data = response.json()
        col = next(col for col in data["columns"] if col["column_name"] == column_name)
        assert col["confidence_bucket"] == expected_bucket


class TestHarmonizeContract:
    """POST /stage-3/harmonize triggers harmonization and returns job info."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Harmonize response includes all HarmonizeResponse schema fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        # When
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": file_id,
                "target_schema": TEST_TARGET_SCHEMA,
                "manual_overrides": {},
            },
        )

        # Then
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "detail" in data
        assert "next_stage_url" in data

    async def test_status_is_valid_value(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Status is one of the expected harmonization states."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        # When
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": file_id,
                "target_schema": TEST_TARGET_SCHEMA,
                "manual_overrides": {},
            },
        )

        # Then
        status = response.json()["status"]
        assert status in ("succeeded", "queued", "running", "failed")


class TestRowsContract:
    """POST /stage-4/rows returns harmonized row comparisons."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Rows response includes StageFourResultsResponse fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {})

        # When
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then
        assert response.status_code == 200
        data = response.json()
        assert "rows" in data
        assert isinstance(data["rows"], list)

    async def test_row_structure(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each row has StageFourRow fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {})

        # When
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then
        rows = response.json()["rows"]
        assert len(rows) > 0
        for row in rows:
            assert "rowNumber" in row
            assert "recordId" in row
            assert "cells" in row

    async def test_cell_structure(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each cell has StageFourCell fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {})

        # When
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then
        cells = response.json()["rows"][0]["cells"]
        for cell in cells:
            assert "columnKey" in cell
            assert "columnLabel" in cell
            assert "originalValue" in cell
            assert "harmonizedValue" in cell
            assert "bucket" in cell
            assert "confidence" in cell
            assert "isChanged" in cell


class TestSummaryContract:
    """POST /stage-5/summary returns change statistics."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Summary response includes all StageFiveSummaryResponse fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {})

        # When
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then
        assert response.status_code == 200
        data = response.json()
        assert "total_rows" in data
        assert "columns_reviewed" in data
        assert "ai_changes" in data
        assert "manual_changes" in data
        assert "column_summaries" in data
        assert "ai_examples" in data
        assert "manual_examples" in data

    async def test_column_summary_structure(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each column summary has ColumnSummary fields."""

        # Given
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {})

        # When
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then
        summaries = response.json()["column_summaries"]
        assert len(summaries) > 0
        for summary in summaries:
            assert "column" in summary
            assert "ai_changes" in summary
            assert "manual_changes" in summary
            assert "unchanged" in summary
