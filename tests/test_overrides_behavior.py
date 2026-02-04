"""Behavioral tests for override propagation, whitespace handling, and metrics normalization."""

from __future__ import annotations

import csv
import io
import zipfile
from io import BytesIO

import pytest
from httpx import AsyncClient

from src.domain.manifest import read_manifest_parquet
from src.domain.storage import UploadStorage
from tests.conftest import (
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    create_test_manifest_parquet,
    review_state_payload,
    upload_content,
)

pytestmark = pytest.mark.asyncio


async def test_stage4_rows_include_grouped_indices(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["col_a", "col_b"],
        ["Foo", "Bar"],
        ["Foo", "Bar"],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "dupes.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})

    assert response.status_code == 200
    data = response.json()
    # Column-centric: check that transformations include grouped row indices
    assert len(data["columns"]) >= 1
    col = data["columns"][0]
    assert len(col["transformations"]) == 1  # "Foo" appears in both rows, grouped
    assert col["transformations"][0]["rowIndices"] == [1, 2]


async def test_download_applies_override_per_column_term(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["record_id", "col_a", "col_b"],
        ["r1", "Foo", "X"],
        ["r2", "Foo", "Y"],
        ["r3", "Bar", "Foo"],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "terms.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})

    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_a": {"ai_value": "Foo", "human_value": "Baz", "original_value": "Foo"}},
            "2": {"col_a": {"ai_value": "Foo", "human_value": "Baz", "original_value": "Foo"}},
        },
        "review_state": review_state_payload(),
    }
    save_response = await app_client.post("/stage-4/overrides", json=overrides_payload)
    assert save_response.status_code == 200

    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert download_response.status_code == 200

    with zipfile.ZipFile(BytesIO(download_response.content), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")

    reader = csv.DictReader(io.StringIO(csv_content))
    output_rows = list(reader)
    assert output_rows[0]["col_a"] == "Baz"
    assert output_rows[1]["col_a"] == "Baz"
    assert output_rows[2]["col_a"] == "Bar"
    assert output_rows[2]["col_b"] == "Foo"


async def test_stage4_preserves_whitespace_values_in_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["col_a"],
        ["  Foo "],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "whitespace.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})
    assert rows_response.status_code == 200
    # Column-centric: get the first transformation from the first column
    columns = rows_response.json()["columns"]
    transformation = columns[0]["transformations"][0]
    assert transformation["originalValue"] == "  Foo "

    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_a": {"ai_value": "  Foo ", "human_value": "Bar", "original_value": "  Foo "}},
        },
        "review_state": review_state_payload(),
    }
    save_response = await app_client.post("/stage-4/overrides", json=overrides_payload)
    assert save_response.status_code == 200

    manifest_path = temp_storage.load_harmonization_manifest_path(file_id)
    assert manifest_path is not None
    summary = read_manifest_parquet(manifest_path)
    assert summary is not None
    matching = [row for row in summary.rows if row.column_name == "col_a" and row.to_harmonize == "  Foo "]
    assert matching
    assert matching[0].manual_overrides[-1].value == "Bar"


async def test_stage4_handles_bom_headers(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    content = "\ufeffrecord_id,col_a\nRID-1,Foo\n".encode()
    file_id = await upload_content(app_client, content, "bom.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})
    assert rows_response.status_code == 200
    # Column-centric: verify columns load correctly with BOM-prefixed headers
    columns = rows_response.json()["columns"]
    assert len(columns) >= 1
    # Verify the col_a column has the expected transformation
    col_a = next((c for c in columns if c["columnKey"] == "col_a"), None)
    assert col_a is not None
    assert col_a["transformations"][0]["originalValue"] == "Foo"


async def test_summary_ignores_case_whitespace_changes(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["col_a"],
        ["Foo"],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "metrics.csv")

    manifest_dir = temp_storage._base_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{file_id}_harmonization.parquet"
    create_test_manifest_parquet(
        manifest_path,
        [{
            "job_id": f"test-job-{file_id}",
            "column_id": 0,
            "column_name": "col_a",
            "to_harmonize": "Foo",
            "top_harmonization": " foo ",
            "ontology_id": None,
            "top_harmonizations": [" foo "],
            "confidence_score": 0.9,
            "error": None,
            "row_indices": [0],
            "manual_overrides": [],
        }],
    )

    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})
    assert summary_response.status_code == 200
    column_summary = summary_response.json()["column_summaries"][0]
    assert column_summary["ai_changes"] == 0
    assert column_summary["manual_changes"] == 0
