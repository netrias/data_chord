"""Validate HTTP API request/response shapes per endpoint through contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

import src.domain.dependencies as dependencies
from src.domain.storage import UploadStorage, WorkflowFile
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_TARGET_SCHEMA,
    TEST_TSV_CONTENT_TYPE,
    TEST_XLSX_CONTENT_TYPE,
    create_harmonized_csv,
    create_manifest_for_file,
    create_xlsx_content,
    store_test_harmonization_manifest,
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

        # Given: A valid CSV file ready for upload

        # When: The file is uploaded via POST
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
        )

        # Then: Response contains all required UploadResponse fields
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

        # Given: A valid CSV file ready for upload

        # When: The file is uploaded via POST
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
        )

        # Then: file_id is a valid hex string suitable for URL paths
        file_id = response.json()["file_id"]
        assert len(file_id) >= 8
        assert all(c in "0123456789abcdef" for c in file_id)

    @pytest.mark.parametrize(
        ("filename", "content_type", "expected_status"),
        [
            ("test.csv", "text/csv", 201),
            ("test.csv", "application/csv", 201),
            ("test.tsv", TEST_TSV_CONTENT_TYPE, 201),
            ("test.xlsx", TEST_XLSX_CONTENT_TYPE, 415),
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

        # Given: A file with specific filename and content type (parameterized)

        # When: The file is uploaded via POST
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": (filename, b"col1,col2\na,b", content_type)},
        )

        # Then: Response status matches expected (201 for CSV, 415 for others)
        assert response.status_code == expected_status

    async def test_xlsx_upload_returns_sheet_metadata(
        self,
        app_client: AsyncClient,
    ) -> None:
        """XLSX uploads expose workbook sheets and default to the first sheet."""

        # Given: a workbook with two sheets ready for upload
        content = create_xlsx_content({
            "First": [["ignored"], ["nope"]],
            "Patients": [["col_a"], ["alpha"]],
        })

        # When: the workbook is uploaded
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("workbook.xlsx", content, TEST_XLSX_CONTENT_TYPE)},
        )

        # Then: upload succeeds and reports sheet selection metadata
        assert response.status_code == 201
        data = response.json()
        assert data["tabular_format"] == "xlsx"
        assert data["sheet_names"] == ["First", "Patients"]
        assert data["selected_sheet"] == "First"


class TestAnalyzeContract:
    """POST /stage-1/analyze returns column metadata and CDE suggestions."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Analyze response includes all AnalyzeResponse schema fields."""

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)

        # When: The file is analyzed
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "target_external_version_number": "11.0.4"},
        )

        # Then: Response contains all required AnalyzeResponse fields
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

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)

        # When: The file is analyzed
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "target_external_version_number": "11.0.4"},
        )

        # Then: Each column contains all required ColumnPreview fields
        columns = response.json()["columns"]
        assert len(columns) > 0
        for col in columns:
            assert "column_name" in col
            assert "column_key" in col
            assert "source_index" in col
            assert "header" in col
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

        # Given: An uploaded CSV with columns of different data types
        file_id = await upload_file(app_client, types_csv_path)

        # When: The file is analyzed
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "target_external_version_number": "11.0.4"},
        )

        # Then: Column type is correctly inferred (numeric, date, or text)
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

        # Given: An uploaded CSV with columns having different null ratios
        file_id = await upload_file(app_client, with_nulls_csv_path)

        # When: The file is analyzed
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "target_external_version_number": "11.0.4"},
        )

        # Then: Confidence bucket reflects data quality (high/medium/low)
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

        # Given: An uploaded and analyzed CSV file
        file_id = await upload_file(app_client, sample_csv_path)
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "target_external_version_number": "11.0.4"},
        )

        # When: Harmonization is triggered
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": file_id,
                "target_schema": TEST_TARGET_SCHEMA,
                "target_external_version_number": "11.0.4",
                "manual_overrides": {},
            },
        )

        # Then: Response contains all required HarmonizeResponse fields
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "detail" in data
        assert "next_stage_url" in data
        assert "elapsed_seconds" in data

    async def test_status_is_valid_value(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Status is one of the expected harmonization states."""

        # Given: An uploaded and analyzed CSV file
        file_id = await upload_file(app_client, sample_csv_path)
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "target_external_version_number": "11.0.4"},
        )

        # When: Harmonization is triggered
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": file_id,
                "target_schema": TEST_TARGET_SCHEMA,
                "target_external_version_number": "11.0.4",
                "manual_overrides": {},
            },
        )

        # Then: Status is one of the valid harmonization states
        status = response.json()["status"]
        assert status in ("succeeded", "queued", "running", "failed")


class TestRowsContract:
    """POST /stage-4/rows returns column-centric harmonization data."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Response includes StageFourResultsResponse fields with columns array."""

        # Given: An uploaded file with harmonized output available
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

        # When: Rows are requested for review
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: Response contains columns array (not rows)
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert isinstance(data["columns"], list)
        assert "columnPVs" in data
        assert "totalOriginalRows" in data

    async def test_column_structure(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each column has ColumnReviewData fields."""

        # Given: An uploaded file with harmonized output available
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

        # When: Rows are requested for review
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: Each column contains required ColumnReviewData fields
        columns = response.json()["columns"]
        assert len(columns) > 0
        for column in columns:
            assert "columnKey" in column
            assert "columnLabel" in column
            assert "sourceColumnIndex" in column
            assert "termCount" in column
            assert "termsWithChanges" in column
            assert "transformations" in column

    async def test_column_includes_target_cde_label_from_mapping_artifact(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Stage 4 columns expose the target CDE reviewers are selecting values against."""

        # Given: a harmonized file has a saved mapping artifact for the first source column
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
        dependencies.get_workflow_storage().write_json(
            dependencies.get_user_context(),
            file_id,
            WorkflowFile.CDE_MAPPING,
            {
                "file_id": file_id,
                "generated_at": "2026-06-02T00:00:00+00:00",
                "target_schema": TEST_TARGET_SCHEMA,
                "target_version": "1",
                "mappings": [{
                    "column_key": "col_0000",
                    "source_column_name": "col_a",
                    "output_column_name": "col_a",
                    "cde_key": "primary_diagnosis",
                    "cde_description": "Primary Diagnosis",
                    "mapping_source": "ai",
                    "maps_values": True,
                }],
            },
        )

        # When: rows are requested for review
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: the column carries both stable identity and reviewer-facing label
        assert response.status_code == 200
        columns = response.json()["columns"]
        assert len(columns) > 0
        first_column = next(column for column in columns if column["columnKey"] == "col_0000")
        assert first_column["targetCdeKey"] == "primary_diagnosis"
        assert first_column["targetCdeLabel"] == "primary_diagnosis"

    async def test_transformation_structure(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each transformation has Transformation fields."""

        # Given: An uploaded file with harmonized output available
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

        # When: Rows are requested for review
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: Each transformation contains required Transformation fields
        columns = response.json()["columns"]
        assert len(columns) > 0
        transformations = columns[0]["transformations"]
        assert len(transformations) > 0
        for t in transformations:
            assert "originalValue" in t
            assert "harmonizedValue" in t
            assert "bucket" in t
            assert "confidence" in t
            assert "isChanged" in t
            assert "recommendationType" in t
            assert "rowIndices" in t
            assert "rowCount" in t


class TestRowContextContract:
    """POST /stage-4/row-context returns original spreadsheet rows for context."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Row context response includes headers and rows arrays."""

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)

        # Negative assertion: no rows have been fetched yet
        # (this is the first request for row context)

        # When: Row context is requested for specific rows
        response = await app_client.post(
            "/stage-4/row-context",
            json={"file_id": file_id, "row_indices": [0, 1, 2]},
        )

        # Then: Response contains headers and rows arrays
        assert response.status_code == 200
        data = response.json()
        assert "headers" in data
        assert "rows" in data
        assert isinstance(data["headers"], list)
        assert isinstance(data["rows"], list)
        assert len(data["headers"]) > 0
        assert len(data["rows"]) == 3

    async def test_row_values_match_headers(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Each row has same number of values as headers."""

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)

        # When: Row context is requested
        response = await app_client.post(
            "/stage-4/row-context",
            json={"file_id": file_id, "row_indices": [0]},
        )

        # Then: Row value count matches header count
        data = response.json()
        headers = data["headers"]
        rows = data["rows"]
        assert len(rows) == 1
        assert len(rows[0]) == len(headers)

    async def test_invalid_file_id_returns_404(
        self,
        app_client: AsyncClient,
    ) -> None:
        """Non-existent file_id returns 404."""

        # Given: A file_id that doesn't exist
        fake_file_id = "deadbeef12345678deadbeef12345678"

        # When: Row context is requested with invalid file_id
        response = await app_client.post(
            "/stage-4/row-context",
            json={"file_id": fake_file_id, "row_indices": [0]},
        )

        # Then: Server returns 404
        assert response.status_code == 404

    async def test_negative_row_index_rejected(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Negative row indices are rejected by validation."""

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)

        # When: Row context is requested with negative index
        response = await app_client.post(
            "/stage-4/row-context",
            json={"file_id": file_id, "row_indices": [-1]},
        )

        # Then: Server returns 422 validation error
        assert response.status_code == 422

    async def test_out_of_bounds_indices_filtered(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Out-of-bounds indices are silently filtered, returning available rows."""

        # Given: An uploaded CSV file (sample.csv has 10 rows)
        file_id = await upload_file(app_client, sample_csv_path)

        # When: Row context is requested with mix of valid and out-of-bounds indices
        response = await app_client.post(
            "/stage-4/row-context",
            json={"file_id": file_id, "row_indices": [0, 5, 1000]},
        )

        # Then: Only valid rows are returned (indices 0 and 5)
        assert response.status_code == 200
        data = response.json()
        assert len(data["rows"]) == 2


class TestTermRowIndicesContract:
    """POST /stage-4/term-row-indices returns all manifest row indices for one term."""

    async def test_matching_term_returns_manifest_row_indices(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Term row indices preserve the 0-based manifest contract used by the browser."""

        # Given: A manifest has grouped source rows for one original term
        file_id = await upload_file(app_client, sample_csv_path)
        assert temp_storage.load_harmonization_manifest_path(file_id) is None
        store_test_harmonization_manifest(
            temp_storage,
            file_id,
            [{
                "column_id": 0,
                "column_name": "diagnosis",
                "to_harmonize": "Bad",
                "top_harmonization": "Allowed",
                "row_indices": [0, 5, 8],
            }],
        )

        # When: the browser asks for full row indices for that term
        response = await app_client.post(
            "/stage-4/term-row-indices",
            json={"file_id": file_id, "column_key": "col_0000", "original_value": "Bad"},
        )

        # Then: the exact 0-based manifest row indices are returned
        assert response.status_code == 200
        assert response.json() == {"row_indices": [0, 5, 8]}

    async def test_unknown_term_returns_empty_indices(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """A valid manifest with no matching term returns an empty list."""

        # Given: A manifest exists for a different original term
        file_id = await upload_file(app_client, sample_csv_path)
        assert temp_storage.load_harmonization_manifest_path(file_id) is None
        store_test_harmonization_manifest(
            temp_storage,
            file_id,
            [{
                "column_id": 0,
                "column_name": "diagnosis",
                "to_harmonize": "Different",
                "top_harmonization": "Allowed",
                "row_indices": [1],
            }],
        )

        # When: the browser asks for an unknown term
        response = await app_client.post(
            "/stage-4/term-row-indices",
            json={"file_id": file_id, "column_key": "col_0000", "original_value": "Bad"},
        )

        # Then: the endpoint returns an empty list rather than an error
        assert response.status_code == 200
        assert response.json() == {"row_indices": []}

    async def test_missing_manifest_returns_404(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """The endpoint preserves the current 404 when Stage 3 has not stored a manifest."""

        # Given: An uploaded file has no harmonization manifest yet
        file_id = await upload_file(app_client, sample_csv_path)
        assert dependencies.get_upload_storage().load_harmonization_manifest_path(file_id) is None

        # When: the browser asks for term row indices
        response = await app_client.post(
            "/stage-4/term-row-indices",
            json={"file_id": file_id, "column_key": "col_0000", "original_value": "Bad"},
        )

        # Then: the endpoint reports the missing manifest
        assert response.status_code == 404
        assert response.json()["detail"] == "Manifest not found"


class TestSummaryContract:
    """POST /stage-5/summary returns change statistics."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Summary response includes all StageFiveSummaryResponse fields."""

        # Given: An uploaded file with harmonized output and manifest available
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: Response contains column summaries
        assert response.status_code == 200
        data = response.json()
        assert "column_summaries" in data
        assert len(data["column_summaries"]) > 0

    async def test_column_summary_structure(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each column summary has ColumnSummary fields."""

        # Given: An uploaded file with harmonized output and manifest available
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: Each column summary contains required ColumnSummary fields
        summaries = response.json()["column_summaries"]
        assert len(summaries) > 0
        for summary in summaries:
            assert "column" in summary
            assert "distinct_terms" in summary
            assert "ai_changes" in summary
            assert "manual_changes" in summary
