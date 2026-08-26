"""Validate HTTP API request/response shapes per endpoint through contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
from src.domain.columns import column_key_from_string
from src.domain.manifest import ColumnMappingRecord
from src.persistence.workflow_state_store import load_workflow_state, save_workflow_state
from src.storage import UploadStorage, WorkflowFile
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_TARGET_SCHEMA,
    TEST_TSV_CONTENT_TYPE,
    TEST_XLSX_CONTENT_TYPE,
    confirm_mapping_choices,
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    create_xlsx_content,
    store_test_completed_harmonization,
    store_test_harmonization_manifest,
    upload_content,
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
            json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
        )

        # Then: Response contains all required AnalyzeResponse fields
        assert response.status_code == 200
        data = response.json()
        assert data["file_id"] == file_id
        assert "file_name" in data
        assert "total_rows" in data
        assert "columns" in data
        assert "cde_targets" in data

    async def test_columns_have_required_fields(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Each column in response has column summary fields."""

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)

        # When: The file is analyzed
        response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
        )

        # Then: Each column contains all required summary fields
        columns = response.json()["columns"]
        assert len(columns) > 0
        for col in columns:
            assert "column_name" in col
            assert "column_key" in col
            assert "source_index" in col
            assert "header" in col
            assert "inferred_type" in col
            assert "has_non_empty_values" in col
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
            json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
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
            json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
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
            json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
        )
        await confirm_mapping_choices(app_client, file_id)

        # When: Harmonization is triggered
        response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
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
            json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
        )
        await confirm_mapping_choices(app_client, file_id)

        # When: Harmonization is triggered
        response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
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
            json={"file_id": file_id},
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
            json={"file_id": file_id},
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

    async def test_column_includes_target_cde_label_from_workflow_state(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Stage 4 exposes the target CDE from the canonical workflow plan."""

        # Given: a harmonized file whose canonical plan maps the first source column
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        harmonized_path = create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
        workflow_storage = dependencies.get_workflow_storage()
        user = dependencies.get_user_context()
        loaded = load_workflow_state(workflow_storage, user, file_id)
        assert loaded is not None
        manifest = loaded.state.mapping_manifest
        assert manifest is not None
        column_key = column_key_from_string("col_0000")
        updated_manifest = manifest.with_record(
            ColumnMappingRecord(
                column_key=column_key,
                column_name="col_a",
                cde_key="primary_diagnosis",
                cde_id=2,
            )
        )
        save_workflow_state(
            workflow_storage,
            user,
            loaded.state.with_mapping_manifest(updated_manifest),
            expected_version=loaded.version,
        )
        store_test_completed_harmonization(temp_storage, file_id, harmonized_path)

        # When: rows are requested for review
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id},
        )

        # Then: the column carries both stable identity and reviewer-facing label
        assert response.status_code == 200
        columns = response.json()["columns"]
        assert len(columns) > 0
        first_column = next(column for column in columns if column["columnKey"] == "col_0000")
        assert first_column["targetCdeKey"] == "primary_diagnosis"
        assert first_column["targetCdeLabel"] == "primary_diagnosis"

    async def test_stage4_omits_unchanged_passthrough_columns(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
    ) -> None:
        """Race and ethnicity pass-throughs are not selectable review columns."""

        def _manifest_row(column_id: int, name: str, value: str) -> dict[str, object]:
            return {
                "column_id": column_id,
                "column_name": name,
                "to_harmonize": value,
                "top_harmonization": "",
                "row_indices": [0],
            }

        def _mapping_entry(
            column_id: int,
            name: str,
            cde_key: str,
            maps_values: bool,
        ) -> dict[str, object]:
            return {
                "column_key": f"col_{column_id:04d}",
                "source_column_name": name,
                "output_column_name": name,
                "cde_key": cde_key,
                "mapping_source": "ai",
                "maps_values": maps_values,
            }

        # Given: Race and ethnicity are unchanged pass-through mappings while
        # diagnosis is a true value-mapping CDE with no AI recommendation.
        upload_response = await app_client.post(
            "/stage-1/upload",
            files={
                "file": (
                    "demographics.csv",
                    b"race,ethnicity,diagnosis\nAsian,Hispanic,Lung\n",
                    TEST_CSV_CONTENT_TYPE,
                ),
            },
        )
        file_id = upload_response.json()["file_id"]
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        store_test_harmonization_manifest(
            temp_storage,
            file_id,
            [
                _manifest_row(0, "race", "Asian"),
                _manifest_row(1, "ethnicity", "Hispanic"),
                _manifest_row(2, "diagnosis", "Lung"),
            ],
        )
        dependencies.get_workflow_storage().write_json(
            dependencies.get_user_context(),
            file_id,
            WorkflowFile.CDE_MAPPING,
            {
                "file_id": file_id,
                "generated_at": "2026-08-24T00:00:00+00:00",
                "data_model_key": TEST_TARGET_SCHEMA,
                "external_version_number": "11.0.4",
                "mappings": [
                    _mapping_entry(0, "race", "race", False),
                    _mapping_entry(1, "ethnicity", "ethnicity", False),
                    _mapping_entry(2, "diagnosis", "primary_diagnosis", True),
                ],
            },
        )

        # When: Stage 4 builds its selectable review columns.
        response = await app_client.post("/stage-4/rows", json={"file_id": file_id})

        # Then: unchanged pass-throughs are omitted, but the true
        # no-recommendation harmonization remains reviewable.
        assert response.status_code == 200
        assert [column["columnLabel"] for column in response.json()["columns"]] == ["diagnosis"]
        assert response.json()["columns"][0]["transformations"][0]["recommendationType"] == "no_recommendation"

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
            json={"file_id": file_id},
        )

        # Then: Each transformation contains required Transformation fields
        columns = response.json()["columns"]
        assert len(columns) > 0
        transformations = columns[0]["transformations"]
        assert len(transformations) > 0
        for t in transformations:
            assert "originalValue" in t
            assert "harmonizedValue" in t
            assert "matchFidelity" in t
            assert t["matchFidelity"] in ("strong", "partial", "approximate", "none")
            assert "isChanged" in t
            assert "recommendationType" in t
            assert "rowIndices" in t


class TestRowContextContract:
    """POST /stage-4/row-context returns original spreadsheet rows for context."""

    async def test_response_contains_required_fields(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Row context response includes headers and rows arrays."""

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        harmonized_path = create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        store_test_completed_harmonization(temp_storage, file_id, harmonized_path)

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
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each row has same number of values as headers."""

        # Given: An uploaded CSV file
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        harmonized_path = create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        store_test_completed_harmonization(temp_storage, file_id, harmonized_path)

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
        """Non-existent workflow returns recovery guidance."""

        # Given: A file_id that doesn't exist
        fake_file_id = "deadbeef12345678deadbeef12345678"

        # When: Row context is requested with invalid file_id
        response = await app_client.post(
            "/stage-4/row-context",
            json={"file_id": fake_file_id, "row_indices": [0]},
        )

        assert response.status_code == 409

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
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Out-of-bounds indices are silently filtered, returning available rows."""

        # Given: An uploaded CSV file (sample.csv has 10 rows)
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        harmonized_path = create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        store_test_completed_harmonization(temp_storage, file_id, harmonized_path)

        # When: Row context is requested with mix of valid and out-of-bounds indices
        response = await app_client.post(
            "/stage-4/row-context",
            json={"file_id": file_id, "row_indices": [0, 5, 1000]},
        )

        # Then: Only valid rows are returned (indices 0 and 5)
        assert response.status_code == 200
        data = response.json()
        assert len(data["rows"]) == 2


class TestSummaryContract:
    """POST /stage-5/summary returns change statistics."""

    async def test_response_describes_current_output_in_source_column_order(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
    ) -> None:
        """One complete response proves metadata, metrics, mappings, and order."""

        # Given: two source columns with one repeated changed value in the first column
        content = create_csv_content([
            ["later", "first"],
            ["zeta", "Alpha"],
            ["zeta", "Beta"],
            ["eta", "Beta"],
        ])
        file_id = await upload_content(app_client, content, "contract.csv")
        meta = temp_storage.load(file_id)
        assert meta is not None
        changes = {
            0: {"later": "Zeta", "first": "Alpha"},
            1: {"later": "Zeta"},
        }
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, changes)
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, changes)

        # When: the current output summary is requested
        response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

        # Then: the public response contains exact source-order outcomes
        assert response.status_code == 200
        data = response.json()
        assert data["dataset"] == {
            "filename": "contract.csv",
            "tabular_format": "csv",
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
        }
        assert data["column_summaries"] == [
            {
                "column": "later",
                "column_key": "col_0000",
                "source_column_index": 0,
                "distinct_terms": 2,
                "changed_distinct_values": 1,
                "total_rows": 3,
                "changed_rows": 2,
                "reviewer_edited_rows": 0,
                "non_conformant_values": 0,
                "review_status": "not_checked",
                "ai_changes": 1,
                "manual_changes": 0,
                "unchanged": 1,
            },
            {
                "column": "first",
                "column_key": "col_0001",
                "source_column_index": 1,
                "distinct_terms": 2,
                "changed_distinct_values": 0,
                "total_rows": 3,
                "changed_rows": 0,
                "reviewer_edited_rows": 0,
                "non_conformant_values": 0,
                "review_status": "not_checked",
                "ai_changes": 0,
                "manual_changes": 0,
                "unchanged": 2,
            },
        ]
        assert [
            {
                "column_key": mapping["column_key"],
                "original_value": mapping["original_value"],
                "final_value": mapping["final_value"],
                "is_changed": mapping["is_changed"],
                "final_value_source": mapping["final_value_source"],
                "review_status": mapping["review_status"],
                "row_count": mapping["row_count"],
            }
            for mapping in data["term_mappings"]
        ] == [
            {
                "column_key": "col_0000",
                "original_value": "eta",
                "final_value": "eta",
                "is_changed": False,
                "final_value_source": "source",
                "review_status": "not_checked",
                "row_count": 1,
            },
            {
                "column_key": "col_0000",
                "original_value": "zeta",
                "final_value": "Zeta",
                "is_changed": True,
                "final_value_source": "data_chord",
                "review_status": "not_checked",
                "row_count": 2,
            },
            {
                "column_key": "col_0001",
                "original_value": "Alpha",
                "final_value": "Alpha",
                "is_changed": False,
                "final_value_source": "source",
                "review_status": "not_checked",
                "row_count": 1,
            },
            {
                "column_key": "col_0001",
                "original_value": "Beta",
                "final_value": "Beta",
                "is_changed": False,
                "final_value_source": "source",
                "review_status": "not_checked",
                "row_count": 2,
            },
        ]
        assert data["non_conformant_items"] == []
