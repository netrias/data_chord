"""Full-flow feature tests across upload, analyze, harmonize, review, and download."""

from __future__ import annotations

import csv
import io
import zipfile
from io import BytesIO

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
from src.app.session_cache import clear_all_session_caches
from src.auth.user_context import ALB_IDENTITY_HEADER
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_cde_map import ColumnCdeMap
from src.domain.pv_manifest import PVManifest
from src.storage import UploadStorage, WorkflowFile
from tests.conftest import (
    TEST_TARGET_SCHEMA,
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    review_state_payload,
    store_test_harmonization_manifest,
    upload_content,
)

pytestmark = pytest.mark.asyncio


def _read_downloaded_csv(response_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")
    return list(csv.DictReader(io.StringIO(csv_content)))


def _read_downloaded_csv_rows(response_bytes: bytes) -> list[list[str]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")
    return list(csv.reader(io.StringIO(csv_content)))


def _save_test_pv_manifest(file_id: str, column_key: str, cde_key: str, pvs: list[str]) -> None:
    dependencies.get_workflow_storage().write_json(
        dependencies.get_user_context(),
        file_id,
        WorkflowFile.PV_MANIFEST,
        PVManifest(
            data_model_key=TEST_TARGET_SCHEMA,
            external_version_number="11.0.4",
            column_to_cde_key=ColumnCdeMap.from_strings({column_key: cde_key}),
            pvs=CdePvCatalog.from_mapping({cde_key: frozenset(pvs)}),
        ).to_store(),
    )


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
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200

    # When: harmonize is triggered and a manifest with no changes is created
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "manual_overrides": {},
        },
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
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
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200

    # When: harmonize is triggered and review overrides are saved
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "manual_overrides": {},
        },
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})
    assert rows_response.status_code == 200
    columns_data = rows_response.json()["columns"]
    assert columns_data, "Expected review columns for override flow"

    # Find transformations for col_a where originalValue is "Foo"
    row_indices: list[int] = []
    col_a_key = ""
    for col in columns_data:
        if col["columnLabel"] == "col_a":
            col_a_key = col["columnKey"]
            for t in col["transformations"]:
                if t["originalValue"] == "Foo":
                    row_indices.extend(t["rowIndices"])
    row_indices = sorted(set(row_indices))

    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            str(index): {col_a_key: {"ai_value": "Foo", "human_value": "Baz", "original_value": "Foo"}}
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
            json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
        )
        assert analyze_response.status_code == 200
        harmonize_response = await app_client.post(
            "/stage-3/harmonize",
            json={
                "file_id": file_id,
                "data_model_key": TEST_TARGET_SCHEMA,
                "external_version_number": "11.0.4",
                "manual_overrides": {},
            },
        )
        assert harmonize_response.status_code == 200
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
        create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    # When: overrides are saved for the first file only
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_one,
            "overrides": {
                "1": {"col_0000": {"ai_value": "alpha", "human_value": "gamma", "original_value": "alpha"}},
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
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "manual_overrides": {},
        },
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {"col_0000": {"ai_value": "alpha", "human_value": "gamma", "original_value": "alpha"}},
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # When: harmonize is triggered again
    rerun_response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "manual_overrides": {},
        },
    )

    # Then: overrides are cleared
    assert rerun_response.status_code == 200
    overrides_response = await app_client.get(f"/stage-4/overrides/{file_id}")
    assert overrides_response.status_code == 200
    assert overrides_response.json() is None


async def test_reharmonize_cannot_clear_another_users_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """A guessed file id cannot be used to wipe another user's review overrides."""

    # Given: Alice owns a workflow with saved review overrides
    rows = [["col_a"], ["alpha"], ["beta"]]
    alice_headers = {ALB_IDENTITY_HEADER: "alice"}
    bob_headers = {ALB_IDENTITY_HEADER: "bob"}
    upload_response = await app_client.post(
        "/stage-1/upload",
        headers=alice_headers,
        files={"file": ("alice.csv", create_csv_content(rows), "text/csv")},
    )
    assert upload_response.status_code == 201
    file_id = upload_response.json()["file_id"]
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        headers=alice_headers,
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        headers=alice_headers,
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "manual_overrides": {},
        },
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
    save_response = await app_client.post(
        "/stage-4/overrides",
        headers=alice_headers,
        json={
            "file_id": file_id,
            "overrides": {
                "1": {"col_0000": {"ai_value": "alpha", "human_value": "gamma", "original_value": "alpha"}},
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # When: Bob guesses Alice's file id and tries to re-harmonize it
    rerun_response = await app_client.post(
        "/stage-3/harmonize",
        headers=bob_headers,
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "manual_overrides": {},
        },
    )

    # Then: Bob is denied and Alice's overrides remain intact
    assert rerun_response.status_code == 403
    overrides_response = await app_client.get(f"/stage-4/overrides/{file_id}", headers=alice_headers)
    assert overrides_response.status_code == 200
    assert overrides_response.json()["overrides"]["1"]["col_0000"]["human_value"] == "gamma"


async def test_stage4_recovers_pvs_after_session_cache_loss(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Stage 4 review uses durable PV manifest data after cache loss."""

    # Given: harmonization artifacts and a durable PV manifest exist, but cache is empty
    rows = [["col_a"], ["alpha"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "stage4-pvs.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": "Denied"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {"col_a": "Denied"}})
    _save_test_pv_manifest(file_id, "col_0000", "diagnosis_cde", ["Allowed"])
    clear_all_session_caches()

    # When: Stage 4 loads review rows and non-conformant values
    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    non_conformant_response = await app_client.get(f"/stage-4/non-conformant/{file_id}")

    # Then: PV availability and non-conformance are recovered from durable state
    assert rows_response.status_code == 200
    data = rows_response.json()
    assert data["columnPVs"] == {"col_0000": ["Allowed"]}
    transformation = data["columns"][0]["transformations"][0]
    assert transformation["pvSetAvailable"] is True
    assert transformation["isPVConformant"] is False
    assert non_conformant_response.status_code == 200
    assert non_conformant_response.json()["count"] == 1


async def test_stage5_summary_recovers_pvs_after_session_cache_loss(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Stage 5 summary uses durable PV manifest data after cache loss."""

    # Given: a changed harmonization manifest has PV recovery data on disk
    rows = [["col_a"], ["alpha"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "stage5-pvs.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": "Denied"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {"col_a": "Denied"}})
    _save_test_pv_manifest(file_id, "col_0000", "diagnosis_cde", ["Allowed"])
    clear_all_session_caches()

    # When: Stage 5 builds the final summary
    response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then: non-conformance and history conformance use the durable PV manifest
    assert response.status_code == 200
    summary = response.json()
    assert summary["non_conformant_count"] == 1
    assert summary["term_mappings"][0]["is_pv_conformant"] is False
    ai_step = next(step for step in summary["term_mappings"][0]["history"] if step["source"] == "ai")
    assert ai_step["is_pv_conformant"] is False


async def test_full_flow_bom_overrides_apply(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Full flow handles BOM headers and applies overrides."""

    # Given: a BOM-prefixed CSV with repeated term
    content = "\ufeffrecord_id,col_a\nRID-1,Foo\nRID-2,Foo\n".encode()
    file_id = await upload_content(app_client, content, "bom-flow.csv")
    assert temp_storage.load_manifest(file_id) is None
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    harmonize_response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "manual_overrides": {},
        },
    )
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})
    assert rows_response.status_code == 200
    columns_data = rows_response.json()["columns"]
    assert columns_data, "Expected review columns"

    # Find transformations for col_a where originalValue is "Foo"
    row_indices: list[int] = []
    col_a_key = ""
    for col in columns_data:
        if col["columnLabel"] == "col_a":
            col_a_key = col["columnKey"]
            for t in col["transformations"]:
                if t["originalValue"] == "Foo":
                    row_indices.extend(t["rowIndices"])
    row_indices = sorted(set(row_indices))

    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                str(index): {col_a_key: {"ai_value": "Foo", "human_value": "Bar", "original_value": "Foo"}}
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


async def test_full_flow_duplicate_headers_keep_columns_separate(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Duplicate headers use column keys so one duplicate can be changed without touching the other."""

    # Given: a CSV with duplicate headers and distinct values in each duplicate column
    rows = [["name", "name"], ["Alice", "Smith"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "duplicate-headers.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    analyzed_columns = analyze_response.json()["columns"]
    assert [col["column_key"] for col in analyzed_columns] == ["col_0000", "col_0001"]

    meta = temp_storage.load(file_id)
    assert meta is not None
    harmonized_path = temp_storage.harmonized_path_for(file_id, meta.saved_path)
    with harmonized_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)

    store_test_harmonization_manifest(
        temp_storage,
        file_id,
        [
            {
                "job_id": f"test-job-{file_id}",
                "column_id": 0,
                "column_name": "name",
                "to_harmonize": "Alice",
                "top_harmonization": "Alice",
                "row_indices": [0],
            },
            {
                "job_id": f"test-job-{file_id}",
                "column_id": 1,
                "column_name": "name",
                "to_harmonize": "Smith",
                "top_harmonization": "Smith",
                "row_indices": [0],
            },
        ],
    )

    # When: the second duplicate column is overridden
    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "manual_columns": []})
    assert rows_response.status_code == 200
    review_columns = rows_response.json()["columns"]
    assert [col["columnKey"] for col in review_columns] == ["col_0000", "col_0001"]
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {"col_0001": {"ai_value": "Smith", "human_value": "Jones", "original_value": "Smith"}},
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # Then: export keeps duplicate headers and changes only the targeted duplicate column
    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert download_response.status_code == 200
    output_rows = _read_downloaded_csv_rows(download_response.content)
    assert output_rows == [["name", "name"], ["Alice", "Jones"]]
