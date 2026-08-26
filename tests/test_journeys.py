"""Test end-to-end user journeys through the harmonization pipeline."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from src.storage import UploadStorage
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_TARGET_SCHEMA,
    confirm_mapping_choices,
    create_harmonized_csv,
    create_manifest_for_file,
    create_manifest_with_manual_override,
    review_state_payload,
    store_test_completed_harmonization,
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
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: Analysis returns column info and CDE suggestions
    assert analyze_response.status_code == 200
    analyze_data = analyze_response.json()
    assert analyze_data["file_id"] == file_id
    assert len(analyze_data["columns"]) > 0
    assert "cde_targets" in analyze_data


async def test_analyze_to_harmonize_journey(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """User analyzes a file then triggers harmonization."""

    # Given: An uploaded and analyzed file
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    file_id = upload_response.json()["file_id"]

    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    await confirm_mapping_choices(app_client, file_id)

    # When: User triggers harmonization from the confirmed mapping plan
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id},
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
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, changes)
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, changes)

    # When: User fetches review rows to compare original vs harmonized
    rows_response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id},
    )

    # Then: Columns are returned with transformations for review
    assert rows_response.status_code == 200
    columns_data = rows_response.json()
    assert len(columns_data["columns"]) > 0

    first_column = columns_data["columns"][0]
    assert "columnKey" in first_column
    assert "transformations" in first_column
    assert len(first_column["transformations"]) > 0


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
    changes = {
        0: {"primary_diagnosis": "Changed1"},
        1: {"therapeutic_agents": "Changed2"},
    }
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, changes)
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, changes)

    # When: User requests summary of all changes
    summary_response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id},
    )

    # Then: Summary shows column change statistics
    assert summary_response.status_code == 200
    summary_data = summary_response.json()
    assert "column_summaries" in summary_data
    assert len(summary_data["column_summaries"]) > 0


async def test_full_pipeline_journey(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete route journey with production Stage 3 artifact writers."""
    from src.app import dependencies
    from src.app.demo_mode import DEMO_REFERENCE_PATH, DEMO_SAMPLE_PATH
    from src.integrations.agentic_harmonize import AgenticHarmonizeConfig, AgenticHarmonizeService
    from src.integrations.demo_harmonization_cache import DemoHarmonizationCache
    from src.integrations.reference_data_file import FileReferenceDataRepository
    from src.integrations.value_overlap_cde_recommendation import ValueOverlapCdeRecommender

    # Given: Real local services that make every harmonization decision deterministic.
    with monkeypatch.context() as test_services:
        test_services.setattr(
            dependencies,
            "_reference_data_repository",
            FileReferenceDataRepository(DEMO_REFERENCE_PATH),
        )
        test_services.setattr(dependencies, "_cde_recommender", ValueOverlapCdeRecommender())
        test_services.setattr(
            dependencies,
            "_harmonize_service",
            AgenticHarmonizeService(
                AgenticHarmonizeConfig(region="us-east-2", max_workers=2),
                cache=DemoHarmonizationCache(),
            ),
        )
        test_services.setattr(
            "src.integrations.agentic_harmonize.make_provider_client",
            lambda *_args, **_kwargs: pytest.fail("The deterministic cache must prevent provider calls"),
        )

        # When: A user completes every API stage and changes one review value.
        upload = await app_client.post(
            "/stage-1/upload",
            files={"file": (DEMO_SAMPLE_PATH.name, DEMO_SAMPLE_PATH.read_bytes(), TEST_CSV_CONTENT_TYPE)},
        )
        assert upload.status_code == 201, upload.text
        file_id = upload.json()["file_id"]

        analysis = await app_client.post(
            "/stage-1/analyze",
            json={
                "file_id": file_id,
                "data_model_key": "data-chord-demo",
                "external_version_number": "1.0",
            },
        )
        assert analysis.status_code == 200, analysis.text
        await confirm_mapping_choices(app_client, file_id)

        harmonization = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
        assert harmonization.status_code == 200, harmonization.text
        job = harmonization.json()
        for _attempt in range(100):
            if job["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.02)
            job_response = await app_client.get(
                f"/stage-3/jobs/{job['job_id']}",
                params={"file_id": file_id},
            )
            assert job_response.status_code == 200, job_response.text
            job = job_response.json()
        assert job["status"] == "succeeded", job

        rows = await app_client.post("/stage-4/rows", json={"file_id": file_id})
        assert rows.status_code == 200, rows.text
        diagnosis = next(
            column for column in rows.json()["columns"] if column["targetCdeKey"] == "primary_diagnosis"
        )
        unresolved = next(
            item for item in diagnosis["transformations"] if item["originalValue"] == "unknown lesion"
        )
        source_row = str(unresolved["rowIndices"][0])

        saved = await app_client.post(
            "/stage-4/overrides",
            headers={"If-None-Match": "*"},
            json={
                "file_id": file_id,
                "overrides": {
                    source_row: {
                        diagnosis["columnKey"]: {
                            "human_value": "Carcinoma NOS",
                            "original_value": "unknown lesion",
                        }
                    }
                },
                "review_state": review_state_payload(),
            },
        )
        assert saved.status_code == 200, saved.text

        summary = await app_client.post("/stage-5/summary", json={"file_id": file_id})
        download = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then: Summary and download use the real Stage 3 output plus the review decision.
    assert summary.status_code == 200, summary.text
    diagnosis_summary = next(
        column for column in summary.json()["column_summaries"] if column["column_key"] == diagnosis["columnKey"]
    )
    assert diagnosis_summary["manual_changes"] == 1
    assert download.status_code == 200, download.text

    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        csv_name = next(name for name in archive.namelist() if name.endswith(".csv"))
        manifest_name = next(name for name in archive.namelist() if name.endswith("_manifest.json"))
        mapping_name = next(name for name in archive.namelist() if name.endswith("_cde_mapping.json"))
        output_rows = list(csv.DictReader(io.StringIO(archive.read(csv_name).decode("utf-8"))))
        manifest = json.loads(archive.read(manifest_name))
        mapping = json.loads(archive.read(mapping_name))

    assert output_rows[2]["diagnosis"] == "Carcinoma NOS"
    manifest_row = next(
        row for row in manifest if row["column_name"] == "diagnosis" and row["to_harmonize"] == "unknown lesion"
    )
    assert manifest_row["active_values"][source_row] == "Carcinoma NOS"
    assert [event["selected_value"] for event in manifest_row["review_events"]] == [
        "Carcinoma NOS",
    ]
    assert mapping["data_model_key"] == "data-chord-demo"


async def test_manual_overrides_counted_in_summary(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Active reviewer overrides are categorized in current summary statistics."""

    # Given: An uploaded file with audit history and the corresponding active override
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    file_id = upload_response.json()["file_id"]

    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"primary_diagnosis": "User Manual Override"}})
    create_manifest_with_manual_override(temp_storage, file_id, meta.saved_path)
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {
                    "col_0000": {
                        "human_value": "User Manual Override",
                        "original_value": "R001",
                    }
                }
            },
            "review_state": {},
        },
        headers={"If-None-Match": "*"},
    )
    assert save_response.status_code == 200

    # When: Summary is requested
    summary_response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id},
    )

    # Then: Changes are counted as manual override (not AI) in summary
    assert summary_response.status_code == 200
    summary = summary_response.json()
    total_manual_changes = sum(col["manual_changes"] for col in summary["column_summaries"])
    assert total_manual_changes >= 1


async def test_download_returns_zip_with_csv(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Download endpoint returns a zip containing the harmonized CSV."""
    import zipfile
    from io import BytesIO

    # Given: An uploaded file with harmonized output available
    upload_response = await app_client.post(
        "/stage-1/upload",
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    file_id = upload_response.json()["file_id"]
    meta = temp_storage.load(file_id)
    assert meta is not None
    harmonized_path = create_harmonized_csv(temp_storage, file_id, meta.saved_path, {
        0: {"primary_diagnosis": "Harmonized Value"},
    })
    store_test_completed_harmonization(temp_storage, file_id, harmonized_path)

    # When: User requests download
    download_response = await app_client.post(
        "/stage-5/download",
        json={"file_id": file_id},
    )

    # Then: Response is a zip file containing the CSV
    assert download_response.status_code == 200
    assert download_response.headers.get("content-type") == "application/zip"
    assert "attachment" in download_response.headers.get("content-disposition", "")

    zip_content = BytesIO(download_response.content)
    with zipfile.ZipFile(zip_content, "r") as zf:
        names = zf.namelist()
        assert any(name.endswith(".csv") for name in names)
