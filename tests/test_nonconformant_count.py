"""Feature tests for Stage 4 non-conformant PV counting."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
from src.storage import UploadStorage
from tests.conftest import (
    create_csv_content,
    create_harmonized_csv,
    save_test_pvs_by_column,
    store_test_harmonization_manifest,
    upload_content,
)

pytestmark = pytest.mark.asyncio


def _manifest_row(
    *,
    column_id: int,
    column_name: str,
    original: str,
    harmonized: str,
    row_index: int = 0,
) -> dict[str, object]:
    return {
        "job_id": "test-job",
        "column_id": column_id,
        "column_name": column_name,
        "to_harmonize": original,
        "top_harmonization": harmonized,
        "ontology_id": None,
        "top_harmonizations": [harmonized] if harmonized else [],
        "match_fidelity": "strong",
        "error": None,
        "row_indices": [row_index],
        "manual_overrides": [],
    }


def _save_pv_manifest(file_id: str, pvs_by_column_key: dict[str, frozenset[str]]) -> None:
    save_test_pvs_by_column(file_id, pvs_by_column_key)


async def _upload_file_with_manifest(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    manifest_rows: list[dict[str, object]],
    pvs_by_column_key: dict[str, frozenset[str]],
) -> str:
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis", "tissue"], ["Original", "Fresh"]]),
        "non-conformant.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    store_test_harmonization_manifest(temp_storage, file_id, manifest_rows)
    _save_pv_manifest(file_id, pvs_by_column_key)
    return file_id


async def test_non_conformant_endpoint_requires_current_harmonization(
    app_client: AsyncClient,
) -> None:
    """A reviewer gets useful recourse when Stage 3 has not completed."""

    # Given: a file has been uploaded but Stage 3 has not stored a manifest
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Bad Value"]]),
        "missing-manifest.csv",
    )
    assert dependencies.get_upload_storage().load_harmonization_manifest_path(file_id) is None

    # When: Stage 4 asks for the non-conformant values
    response = await app_client.get(f"/stage-4/non-conformant/{file_id}")

    # Then: navigation is blocked with a clear next action
    assert response.status_code == 409
    assert "Stage 2" in response.json()["detail"]


async def test_non_conformant_endpoint_counts_current_unique_bad_values(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Stage 4 counts the current unique values that are outside their column PV set."""

    # Given: a manifest has unchanged, AI-changed, and conformant current values
    manifest_rows = [
        _manifest_row(column_id=0, column_name="diagnosis", original="Bad", harmonized="Bad", row_index=0),
        _manifest_row(
            column_id=0,
            column_name="diagnosis",
            original="Source",
            harmonized="Bad AI",
            row_index=1,
        ),
        _manifest_row(
            column_id=0,
            column_name="diagnosis",
            original="Manual Source",
            harmonized="Allowed Diagnosis",
            row_index=2,
        ),
        _manifest_row(
            column_id=0,
            column_name="diagnosis",
            original="Conformant Source",
            harmonized="Allowed Diagnosis",
            row_index=3,
        ),
    ]
    file_id = await _upload_file_with_manifest(
        app_client,
        temp_storage,
        manifest_rows,
        {"col_0000": frozenset({"Allowed Diagnosis"})},
    )
    assert dependencies.get_upload_storage().load_harmonization_manifest_path(file_id) is not None

    # When: Stage 4 asks for the non-conformant values
    response = await app_client.get(f"/stage-4/non-conformant/{file_id}")

    # Then: only the two current bad values are shown
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["items"] == [
        {"column": "diagnosis", "value": "Bad", "original": "Bad"},
        {"column": "diagnosis", "value": "Bad AI", "original": "Source"},
    ]


async def test_non_conformant_gate_matches_active_per_cell_export_values(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """The gate checks active cell overrides, not the term-level audit tail."""

    # Given: two cells share one source term, but their active review values differ
    source_rows = [["diagnosis"], ["Repeated"], ["Repeated"]]
    file_id = await upload_content(
        app_client,
        create_csv_content(source_rows),
        "per-cell-conformance.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(
        temp_storage,
        file_id,
        meta.saved_path,
        {
            0: {"diagnosis": "Allowed"},
            1: {"diagnosis": "Allowed"},
        },
    )
    manifest_row = _manifest_row(
        column_id=0,
        column_name="diagnosis",
        original="Repeated",
        harmonized="Allowed",
    )
    manifest_row["row_indices"] = [0, 1]
    store_test_harmonization_manifest(temp_storage, file_id, [manifest_row])
    _save_pv_manifest(file_id, {"col_0000": frozenset({"Allowed"})})

    save_response = await app_client.post(
        "/stage-4/overrides",
        headers={"If-None-Match": "*"},
        json={
            "file_id": file_id,
            "overrides": {
                    "1": {
                        "col_0000": {
                            "human_value": "Disallowed",
                            "original_value": "Repeated",
                        },
                    },
                },
            "review_state": {
                "review_mode": "column",
                "sort_mode": "original",
                "scroll_mode": False,
                "show_case_only_changes": False,
                "show_unchanged_values": False,
                "column_mode": {"current_unit": 1, "batch_size": 5},
                "row_mode": {"current_unit": 1, "batch_size": 5},
            },
        },
    )
    assert save_response.status_code == 200

    # When: the reviewer checks the gate and downloads the current output
    gate_response = await app_client.get(f"/stage-4/non-conformant/{file_id}")
    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then: both operations use the same active value for each cell
    assert gate_response.status_code == 200
    assert gate_response.json() == {
        "count": 1,
        "items": [
            {
                "column": "diagnosis",
                "value": "Disallowed",
                "original": "Repeated",
            },
        ],
    }
    assert download_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download_response.content)) as archive:
        csv_name = next(name for name in archive.namelist() if name.endswith(".csv"))
        exported_rows = list(csv.DictReader(io.StringIO(archive.read(csv_name).decode())))
    assert [row["diagnosis"] for row in exported_rows] == ["Disallowed", "Allowed"]


async def test_stage4_and_stage5_report_same_non_conformant_count(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """The review gate and final summary count the same unique bad mappings."""

    # Given: Stage 3 stored a manifest with one repeated bad mapping and one unique bad mapping
    manifest_rows = [
        _manifest_row(column_id=0, column_name="diagnosis", original="Bad", harmonized="Bad", row_index=0),
        _manifest_row(column_id=0, column_name="diagnosis", original="Bad", harmonized="Bad", row_index=1),
        _manifest_row(column_id=0, column_name="diagnosis", original="Original", harmonized="Bad AI", row_index=2),
        _manifest_row(
            column_id=0,
            column_name="diagnosis",
            original="Good",
            harmonized="Allowed Diagnosis",
            row_index=3,
        ),
    ]
    file_id = await _upload_file_with_manifest(
        app_client,
        temp_storage,
        manifest_rows,
        {"col_0000": frozenset({"Allowed Diagnosis"})},
    )
    assert dependencies.get_upload_storage().load_harmonization_manifest_path(file_id) is not None

    # When: Stage 4 and Stage 5 both summarize non-conformant values
    stage4_response = await app_client.get(f"/stage-4/non-conformant/{file_id}")
    stage5_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then: both user-facing stages report the same deduplicated count
    assert stage4_response.status_code == 200
    assert stage5_response.status_code == 200
    assert stage4_response.json()["count"] == 2
    assert stage5_response.json()["non_conformant_count"] == 2


async def test_non_conformant_endpoint_deduplicates_by_column_original_and_final(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Repeated manifest rows for the same mapping count once."""

    # Given: the same non-conformant mapping appears in multiple manifest rows
    manifest_rows = [
        _manifest_row(column_id=0, column_name="diagnosis", original="Bad", harmonized="Bad", row_index=0),
        _manifest_row(column_id=0, column_name="diagnosis", original="Bad", harmonized="Bad", row_index=1),
        _manifest_row(column_id=0, column_name="diagnosis", original="Bad", harmonized="Bad", row_index=2),
    ]
    file_id = await _upload_file_with_manifest(
        app_client,
        temp_storage,
        manifest_rows,
        {"col_0000": frozenset({"Allowed Diagnosis"})},
    )
    assert dependencies.get_upload_storage().load_harmonization_manifest_path(file_id) is not None

    # When: Stage 4 asks for the non-conformant values
    response = await app_client.get(f"/stage-4/non-conformant/{file_id}")

    # Then: the repeated mapping is counted once
    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "items": [{"column": "diagnosis", "value": "Bad", "original": "Bad"}],
    }


async def test_non_conformant_endpoint_ignores_columns_without_pvs_and_empty_values(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Only columns with PVs and non-empty current values can be non-conformant."""

    # Given: one column has PVs, another has none, and one mapped value is empty
    manifest_rows = [
        _manifest_row(column_id=0, column_name="diagnosis", original="Bad", harmonized="Bad", row_index=0),
        _manifest_row(column_id=0, column_name="diagnosis", original="", harmonized="", row_index=1),
        _manifest_row(column_id=1, column_name="free_text", original="Anything", harmonized="Anything", row_index=2),
    ]
    file_id = await _upload_file_with_manifest(
        app_client,
        temp_storage,
        manifest_rows,
        {"col_0000": frozenset({"Allowed Diagnosis"})},
    )
    assert dependencies.get_upload_storage().load_harmonization_manifest_path(file_id) is not None

    # When: Stage 4 asks for the non-conformant values
    response = await app_client.get(f"/stage-4/non-conformant/{file_id}")

    # Then: only the non-empty value from the PV-backed column is counted
    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "items": [{"column": "diagnosis", "value": "Bad", "original": "Bad"}],
    }
