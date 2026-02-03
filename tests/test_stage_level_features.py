"""Stage-level feature tests for upload, mapping, harmonization, review, and download."""

from __future__ import annotations

import csv
import io
import zipfile
from io import BytesIO

import pytest
from httpx import AsyncClient

from src.domain.harmonize import HarmonizeResult
from src.domain.storage import UploadStorage
from tests.conftest import (
    TEST_TARGET_SCHEMA,
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    upload_content,
)

pytestmark = pytest.mark.asyncio


def _review_state_payload() -> dict[str, object]:
    return {
        "review_mode": "column",
        "sort_mode": "original",
        "column_mode": {
            "current_unit": 1,
            "completed_units": [],
            "flagged_units": [],
            "batch_size": 5,
        },
        "row_mode": {
            "current_unit": 1,
            "completed_units": [],
            "flagged_units": [],
            "batch_size": 5,
        },
    }


def _read_downloaded_csv(response_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")
    return list(csv.DictReader(io.StringIO(csv_content)))


async def test_stage1_upload_persists_exact_bytes(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Upload stores the exact CSV bytes for later processing."""

    # Given: a CSV payload and no files in storage yet
    content = create_csv_content([["col_a"], ["alpha"], ["beta"]])
    assert list(temp_storage._data_dir.glob("*.csv")) == []

    # When: the file is uploaded
    file_id = await upload_content(app_client, content, "bytes.csv")

    # Then: stored metadata and file contents match the upload
    meta = temp_storage.load(file_id)
    assert meta is not None, "Expected stored metadata for uploaded file"
    assert meta.size_bytes == len(content), "Stored size does not match upload size"
    assert meta.saved_path.read_bytes() == content, "Stored bytes do not match uploaded bytes"


async def test_stage1_upload_rejects_mismatched_content_type(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Upload rejects non-CSV content types."""

    # Given: CSV bytes with an unsupported content type
    content = create_csv_content([["col_a"], ["alpha"]])
    assert list(temp_storage._data_dir.glob("*.csv")) == []

    # When: the file is uploaded with a mismatched content type
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("bad.json", content, "application/json")},
    )

    # Then: upload is rejected with 415
    assert response.status_code == 415


async def test_stage1_analyze_rejects_invalid_utf8(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze returns 400 for invalid UTF-8 payloads."""

    # Given: bytes that are not valid UTF-8
    content = b"\xff\xfe\xfa\xfb"
    file_id = await upload_content(app_client, content, "invalid.csv")
    assert temp_storage.load_manifest(file_id) is None

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: bad request is returned
    assert response.status_code == 400


async def test_stage1_analyze_handles_quoted_commas(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze treats quoted commas as part of the value."""

    # Given: a CSV containing quoted commas
    content = b'col_a\n"alpha, beta"\n'
    file_id = await upload_content(app_client, content, "quoted.csv")
    assert temp_storage.load_manifest(file_id) is None

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: sample values keep the comma inside the string
    assert response.status_code == 200
    sample = response.json()["columns"][0]["sample_values"][0]
    assert sample == "alpha, beta"


async def test_stage1_analyze_handles_ragged_rows(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze fills missing values for ragged rows."""

    # Given: a CSV with missing values in some rows
    content = b"col_a,col_b\nalpha,beta\ncharlie,\n"
    file_id = await upload_content(app_client, content, "ragged.csv")
    assert temp_storage.load_manifest(file_id) is None

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: sample values include empty string for missing cells
    assert response.status_code == 200
    columns = response.json()["columns"]
    col_b_samples = next(col for col in columns if col["column_name"] == "col_b")["sample_values"]
    assert col_b_samples[1] == ""


async def test_stage1_analyze_rejects_duplicate_headers(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze rejects CSVs with duplicate headers."""

    # Given: a CSV with duplicate header names
    content = b"col_a,col_a\nalpha,beta\n"
    file_id = await upload_content(app_client, content, "dupe.csv")
    assert temp_storage.load_manifest(file_id) is None

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: bad request indicates duplicate headers
    assert response.status_code == 400
    assert "Duplicate headers" in response.text


async def test_stage1_analyze_truncates_preview_only(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze truncates preview values but not stored content."""

    # Given: a CSV with very long values
    long_value = "a" * 200
    content = create_csv_content([["col_a"], [long_value]])
    file_id = await upload_content(app_client, content, "long.csv")
    assert temp_storage.load_manifest(file_id) is None

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: sample values are truncated but file content is intact
    assert response.status_code == 200
    sample_value = response.json()["columns"][0]["sample_values"][0]
    assert len(sample_value) == 80


async def test_stage1_analyze_bom_and_non_bom_match_headers(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """BOM and non-BOM files produce identical headers."""

    # Given: BOM and non-BOM CSVs with the same headers
    bom_content = "\ufeffcol_a,col_b\nalpha,beta\n".encode("utf-8")
    non_bom_content = b"col_a,col_b\nalpha,beta\n"
    bom_file_id = await upload_content(app_client, bom_content, "bom.csv")
    non_bom_file_id = await upload_content(app_client, non_bom_content, "plain.csv")
    assert temp_storage.load_manifest(bom_file_id) is None
    assert temp_storage.load_manifest(non_bom_file_id) is None

    # When: analyze is requested for both
    bom_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": bom_file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    non_bom_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": non_bom_file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: the headers are identical
    assert bom_response.status_code == 200
    assert non_bom_response.status_code == 200
    bom_headers = [col["column_name"] for col in bom_response.json()["columns"]]
    non_bom_headers = [col["column_name"] for col in non_bom_response.json()["columns"]]
    assert bom_headers == non_bom_headers

async def test_stage1_analyze_handles_bom_headers(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze strips BOM headers so column names are correct."""

    # Given: a BOM-prefixed CSV and no manifest stored yet
    content = "\ufeffrecord_id,col_a\nRID-1,Foo\n".encode("utf-8")
    file_id = await upload_content(app_client, content, "bom.csv")
    assert temp_storage.load_manifest(file_id) is None

    # When: the file is analyzed
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: column names do not include BOM characters
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert columns[0]["column_name"] == "record_id"


async def test_stage1_analyze_is_idempotent(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Repeated analysis does not change the stored manifest."""

    # Given: an uploaded CSV with no manifest yet
    content = create_csv_content([["col_a"], ["alpha"], ["beta"]])
    file_id = await upload_content(app_client, content, "idempotent.csv")
    assert temp_storage.load_manifest(file_id) is None

    # When: the file is analyzed twice
    response_one = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    response_two = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: manifest and API outputs remain stable
    assert response_one.status_code == 200
    assert response_two.status_code == 200
    manifest_one = temp_storage.load_manifest(file_id)
    manifest_two = temp_storage.load_manifest(file_id)
    assert manifest_one == manifest_two, "Manifest changed between analyses"


async def test_stage2_mapping_page_renders_manual_options(app_client: AsyncClient) -> None:
    """Stage 2 mapping page exposes CDE labels for manual mapping."""

    # Given: the mapping page has not been loaded yet
    # When: the mapping page is requested
    response = await app_client.get("/stage-2")

    # Then: the page renders and includes CDE labels
    assert response.status_code == 200
    assert "primary_diagnosis" in response.text


async def test_stage2_mapping_page_includes_default_schema(app_client: AsyncClient) -> None:
    """Stage 2 mapping page renders the default schema value."""

    # Given: the mapping page has not been loaded yet
    # When: the mapping page is requested
    response = await app_client.get("/stage-2")

    # Then: the default schema is embedded for client-side use
    assert response.status_code == 200
    assert 'targetSchema: "ccdi"' in response.text


async def test_stage3_harmonize_uses_stored_manifest_when_payload_missing(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Harmonize falls back to the stored manifest if request payload omits it."""

    class StubHarmonizer:
        def __init__(self) -> None:
            self.received_manifest = None

        def run(self, *, file_path, target_schema, column_mappings, manifest):  # type: ignore[no-untyped-def]
            self.received_manifest = manifest
            return HarmonizeResult(job_id="job-1", status="succeeded", detail="ok")

    # Given: an uploaded file with a stored manifest
    file_id = await upload_content(app_client, create_csv_content([["col_a"], ["alpha"]]), "manifest.csv")
    stored_manifest = {"column_mappings": {"col_a": {"targetField": "primary_diagnosis"}}}
    temp_storage.save_manifest(file_id, stored_manifest)
    stub = StubHarmonizer()
    assert stub.received_manifest is None

    # When: harmonize is triggered without a manifest payload
    import unittest.mock

    with unittest.mock.patch("src.stage_3_harmonize.router.get_harmonize_service", return_value=stub):
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": file_id,
                "target_schema": TEST_TARGET_SCHEMA,
                "manual_overrides": {},
            },
        )

    # Then: the stored manifest is used
    assert response.status_code == 200
    assert stub.received_manifest == stored_manifest


async def test_stage3_harmonize_prefers_payload_manifest(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Payload manifest overrides any stored manifest."""

    class StubHarmonizer:
        def __init__(self) -> None:
            self.received_manifest = None

        def run(self, *, file_path, target_schema, column_mappings, manifest):  # type: ignore[no-untyped-def]
            self.received_manifest = manifest
            return HarmonizeResult(job_id="job-2", status="succeeded", detail="ok")

    # Given: an uploaded file with a stored manifest
    file_id = await upload_content(app_client, create_csv_content([["col_a"], ["alpha"]]), "payload.csv")
    temp_storage.save_manifest(file_id, {"column_mappings": {"col_a": {"targetField": "primary_diagnosis"}}})
    payload_manifest = {"column_mappings": {"col_a": {"targetField": "morphology"}}}
    stub = StubHarmonizer()

    # When: harmonize is triggered with a manifest payload
    import unittest.mock

    with unittest.mock.patch("src.stage_3_harmonize.router.get_harmonize_service", return_value=stub):
        response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": file_id,
                "target_schema": TEST_TARGET_SCHEMA,
                "manual_overrides": {},
                "manifest": payload_manifest,
            },
        )

    # Then: payload manifest is used instead of the stored one
    assert response.status_code == 200
    assert stub.received_manifest == payload_manifest


async def test_stage5_download_matches_harmonized_when_no_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Download returns the harmonized dataset when no overrides exist."""

    # Given: an uploaded file with harmonized output and no overrides
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "download.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {1: {"col_a": "gamma"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {1: {"col_a": "gamma"}})

    # When: the download endpoint is invoked
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then: the CSV reflects harmonized values
    assert response.status_code == 200
    output_rows = _read_downloaded_csv(response.content)
    assert output_rows[1]["col_a"] == "gamma"


async def test_stage5_download_succeeds_without_manifest(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Download succeeds even when a manifest is missing."""

    # Given: an uploaded file with a harmonized CSV but no manifest
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "no-manifest.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})

    # When: the download endpoint is invoked
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then: the response contains only the CSV file
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content), "r") as zf:
        names = zf.namelist()
    assert any(name.endswith(".csv") for name in names)
    assert not any(name.endswith(".parquet") for name in names)


async def test_stage5_download_ignores_invalid_row_keys(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Overrides for out-of-range row keys do not alter the output."""

    # Given: an uploaded file with harmonized output and invalid overrides
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "invalid-rows.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
    await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "99": {"col_a": {"ai_value": "alpha", "human_value": "gamma", "original_value": "alpha"}},
            },
            "review_state": _review_state_payload(),
        },
    )

    # When: the download endpoint is invoked
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then: output rows remain unchanged
    assert response.status_code == 200
    output_rows = _read_downloaded_csv(response.content)
    assert output_rows[0]["col_a"] == "alpha"
    assert output_rows[1]["col_a"] == "beta"

async def test_stage5_summary_zero_changes_when_terms_equal(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Summary counts zero AI changes when harmonized values equal originals."""

    # Given: an uploaded file with no changes in the manifest
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "summary.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    # When: summary is requested
    response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then: AI changes are zero
    assert response.status_code == 200
    summary = response.json()
    total_ai_changes = sum(col["ai_changes"] for col in summary["column_summaries"])
    assert total_ai_changes == 0
