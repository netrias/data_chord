"""End-to-end user journey tests for the harmonization pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from src.stage_1_upload.services import UploadStorage
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_TARGET_SCHEMA,
    create_harmonized_csv,
    create_manifest_for_file,
)

pytestmark = pytest.mark.asyncio


async def test_upload_to_analyze_journey(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """User uploads a CSV then analyzes it for column mappings."""

    # Given: A valid CSV file ready for the harmonization pipeline
    csv_content = sample_csv_path.read_bytes()

    # When: User uploads the file
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, csv_content, TEST_CSV_CONTENT_TYPE)},
    )

    # Then: Upload succeeds with file_id for subsequent operations
    assert upload_response.status_code == 201
    file_id = upload_response.json()["file_id"]
    assert len(file_id) >= 8

    # When: User analyzes the uploaded file for column mappings
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: Analysis returns column info, CDE suggestions, and manifest
    assert analyze_response.status_code == 200
    analyze_data = analyze_response.json()
    assert analyze_data["file_id"] == file_id
    assert len(analyze_data["columns"]) > 0
    assert "cde_targets" in analyze_data
    assert "manifest" in analyze_data


async def test_analyze_to_harmonize_journey(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """User analyzes a file then triggers harmonization."""

    # Given: An uploaded and analyzed file with manifest from analysis
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    file_id = upload_response.json()["file_id"]

    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    manifest = analyze_response.json()["manifest"]

    # When: User triggers harmonization with the manifest
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
            "manifest": manifest,
        },
    )

    # Then: Harmonization returns job info with URL to review stage
    assert harmonize_response.status_code == 200
    harmonize_data = harmonize_response.json()
    assert "job_id" in harmonize_data
    assert harmonize_data["status"] in ("succeeded", "queued", "running")
    assert "/stage-4" in harmonize_data["next_stage_url"]


async def test_harmonize_to_review_journey(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """User harmonizes a file then reviews the results."""

    # Given: An uploaded file with harmonized output available for review
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    file_id = upload_response.json()["file_id"]

    meta = temp_storage.load(file_id)
    assert meta is not None
    changes = {0: {"primary_diagnosis": "Harmonized Value"}}
    create_harmonized_csv(meta.saved_path, changes)
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, changes)

    # When: User fetches review rows to compare original vs harmonized
    rows_response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then: Rows are returned with cell comparisons for review
    assert rows_response.status_code == 200
    rows_data = rows_response.json()
    assert len(rows_data["rows"]) > 0

    first_row = rows_data["rows"][0]
    assert "recordId" in first_row
    assert "cells" in first_row
    assert len(first_row["cells"]) > 0


async def test_review_to_summary_journey(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """User reviews rows then gets a summary of all changes."""

    # Given: An uploaded file with harmonized output containing multiple changes
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    file_id = upload_response.json()["file_id"]

    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {
        0: {"primary_diagnosis": "Changed1"},
        1: {"therapeutic_agents": "Changed2"},
    })

    # When: User requests summary of all changes
    summary_response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then: Summary shows aggregate change statistics
    assert summary_response.status_code == 200
    summary_data = summary_response.json()
    assert summary_data["total_rows"] > 0
    assert summary_data["columns_reviewed"] > 0
    assert "ai_changes" in summary_data
    assert "column_summaries" in summary_data


async def test_full_pipeline_journey(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Complete user journey: upload -> analyze -> harmonize -> review -> summary."""

    # Given: A valid CSV file to process through all pipeline stages

    # Stage 1: Upload
    # When: User uploads the CSV file
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    # Then: Upload succeeds
    assert upload_response.status_code == 201
    file_id = upload_response.json()["file_id"]

    # Stage 1: Analyze
    # When: User analyzes the uploaded file
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    # Then: Analysis succeeds with manifest
    assert analyze_response.status_code == 200
    manifest = analyze_response.json()["manifest"]

    # Stage 3: Harmonize
    # When: User triggers harmonization
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
            "manifest": manifest,
        },
    )
    # Then: Harmonization succeeds
    assert harmonize_response.status_code == 200

    # Simulate harmonized output (in production this comes from Netrias)
    meta = temp_storage.load(file_id)
    assert meta is not None
    changes = {
        0: {"primary_diagnosis": "Standardized Diagnosis"},
        1: {"therapeutic_agents": "Standardized Agent"},
    }
    create_harmonized_csv(meta.saved_path, changes)
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, changes)

    # Stage 4: Review rows
    # When: User fetches rows for review
    rows_response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": []},
    )
    # Then: Rows are returned
    assert rows_response.status_code == 200
    assert len(rows_response.json()["rows"]) > 0

    # Stage 5: Summary
    # When: User requests final summary
    summary_response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )
    # Then: Summary shows expected change counts
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["total_rows"] > 0
    assert summary["ai_changes"] >= 2


async def test_manual_columns_flow_through_pipeline(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Manual column designation flows from harmonize through review to summary."""

    # Given: An uploaded file with harmonized output containing changes
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    file_id = upload_response.json()["file_id"]

    meta = temp_storage.load(file_id)
    assert meta is not None
    changes = {0: {"primary_diagnosis": "Manual Override Value"}}
    create_harmonized_csv(meta.saved_path, changes)
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, changes)

    # When: User marks primary_diagnosis as manual column in review
    rows_response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": ["primary_diagnosis"]},
    )

    # Then: Rows are returned with cell data from manifest
    rows_data = rows_response.json()
    changed_cell = None
    for row in rows_data["rows"]:
        for cell in row["cells"]:
            if cell["columnKey"] == "primary_diagnosis" and cell["isChanged"]:
                changed_cell = cell
                break

    assert changed_cell is not None
    assert changed_cell["confidence"] > 0  # confidence comes from manifest

    # When: Summary is requested with same manual columns
    summary_response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": ["primary_diagnosis"]},
    )

    # Then: Changes are counted as manual (not AI) in summary
    summary = summary_response.json()
    assert summary["manual_changes"] >= 1
