"""Full-flow feature tests across upload, analyze, harmonize, review, and download."""

from __future__ import annotations

import csv
import io
import zipfile
from io import BytesIO

import pytest
from httpx import AsyncClient

from src.domain.storage import UploadStorage
from tests.conftest import (
    TEST_TARGET_SCHEMA,
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    review_state_payload,
    upload_content,
)

pytestmark = pytest.mark.asyncio


def _read_downloaded_csv(response_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")
    return list(csv.DictReader(io.StringIO(csv_content)))


async def test_full_flow_no_changes_produces_zero_summary(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Full flow yields zero AI changes when harmonized values equal originals."""

    # Given: a CSV uploaded and analyzed through Stage 1
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "full-no-change.csv")
    assert temp_storage.load_manifest(file_id) is None
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    assert analyze_response.status_code == 200

    # When: harmonize is triggered and a manifest with no changes is created
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "manual_overrides": {}},
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    # Then: summary reports zero AI changes
    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})
    assert summary_response.status_code == 200
    summary = summary_response.json()
    total_ai_changes = sum(col["ai_changes"] for col in summary["column_summaries"])
    assert total_ai_changes == 0


async def test_full_flow_overrides_propagate_within_column(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Full flow propagates overrides across all instances in a column."""

    # Given: a CSV with repeated terms in one column and no overrides yet
    rows = [
        ["record_id", "col_a", "col_b"],
        ["r1", "Foo", "X"],
        ["r2", "Foo", "Y"],
        ["r3", "Bar", "Foo"],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "full-override.csv")
    assert temp_storage.load_manifest(file_id) is None
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    assert analyze_response.status_code == 200

    # When: harmonize is triggered and review overrides are saved
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "manual_overrides": {}},
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})
    assert rows_response.status_code == 200
    rows_data = rows_response.json()["rows"]
    assert rows_data, "Expected review rows for override flow"

    foo_rows = [
        row for row in rows_data
        if any(cell["columnKey"] == "col_a" and cell["originalValue"] == "Foo" for cell in row["cells"])
    ]
    row_indices = sorted({index for row in foo_rows for index in row["sourceRowNumbers"]})

    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            str(index): {"col_a": {"ai_value": "Foo", "human_value": "Baz", "original_value": "Foo"}}
            for index in row_indices
        },
        "review_state": review_state_payload(),
    }
    save_response = await app_client.post("/stage-4/overrides", json=overrides_payload)
    assert save_response.status_code == 200

    # Then: download reflects overrides only within the column
    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert download_response.status_code == 200
    output_rows = _read_downloaded_csv(download_response.content)
    assert output_rows[0]["col_a"] == "Baz"
    assert output_rows[1]["col_a"] == "Baz"
    assert output_rows[2]["col_a"] == "Bar"
    assert output_rows[2]["col_b"] == "Foo"


async def test_full_flow_two_files_isolated_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Overrides for one file do not affect another file."""

    # Given: two uploaded files with harmonized output
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_one = await upload_content(app_client, create_csv_content(rows), "file-one.csv")
    file_two = await upload_content(app_client, create_csv_content(rows), "file-two.csv")
    assert temp_storage.load_manifest(file_one) is None
    assert temp_storage.load_manifest(file_two) is None

    for file_id in (file_one, file_two):
        analyze_response = await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )
        assert analyze_response.status_code == 200
        harmonize_response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "manual_overrides": {}},
        )
        assert harmonize_response.status_code == 200
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    # When: overrides are saved for the first file only
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_one,
            "overrides": {
                "1": {"col_a": {"ai_value": "alpha", "human_value": "gamma", "original_value": "alpha"}},
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # Then: downloads reflect overrides only for the first file
    download_one = await app_client.post("/stage-5/download", json={"file_id": file_one})
    download_two = await app_client.post("/stage-5/download", json={"file_id": file_two})
    assert download_one.status_code == 200
    assert download_two.status_code == 200
    output_one = _read_downloaded_csv(download_one.content)
    output_two = _read_downloaded_csv(download_two.content)
    assert output_one[0]["col_a"] == "gamma"
    assert output_two[0]["col_a"] == "alpha"


async def test_full_flow_reharmonize_clears_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Re-running harmonize clears stale review overrides."""

    # Given: a file with saved review overrides
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "reharmonize.csv")
    assert temp_storage.load_manifest(file_id) is None
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    assert analyze_response.status_code == 200
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "manual_overrides": {}},
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {"col_a": {"ai_value": "alpha", "human_value": "gamma", "original_value": "alpha"}},
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # When: harmonize is triggered again
    rerun_response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "manual_overrides": {}},
    )

    # Then: overrides are cleared
    assert rerun_response.status_code == 200
    overrides_response = await app_client.get(f"/stage-4/overrides/{file_id}")
    assert overrides_response.status_code == 200
    assert overrides_response.json() is None


async def test_full_flow_bom_overrides_apply(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Full flow handles BOM headers and applies overrides."""

    # Given: a BOM-prefixed CSV with repeated term
    content = "\ufeffrecord_id,col_a\nRID-1,Foo\nRID-2,Foo\n".encode("utf-8")
    file_id = await upload_content(app_client, content, "bom-flow.csv")
    assert temp_storage.load_manifest(file_id) is None
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    assert analyze_response.status_code == 200
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA, "manual_overrides": {}},
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})
    assert rows_response.status_code == 200
    rows_data = rows_response.json()["rows"]
    assert rows_data[0]["recordId"] == "RID-1"
    foo_rows = [
        row for row in rows_data
        if any(cell["columnKey"] == "col_a" and cell["originalValue"] == "Foo" for cell in row["cells"])
    ]
    row_indices = sorted({index for row in foo_rows for index in row["sourceRowNumbers"]})

    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                str(index): {"col_a": {"ai_value": "Foo", "human_value": "Bar", "original_value": "Foo"}}
                for index in row_indices
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # Then: download reflects overrides
    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert download_response.status_code == 200
    output_rows = _read_downloaded_csv(download_response.content)
    assert output_rows[0]["col_a"] == "Bar"
    assert output_rows[1]["col_a"] == "Bar"
