"""Feature tests for Stage 4 non-conformant PV counting."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import src.domain.dependencies as dependencies
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_cde_map import ColumnCdeMap
from src.domain.pv_manifest import PVManifest
from src.domain.storage import UploadStorage, WorkflowFile
from tests.conftest import (
    TEST_TARGET_SCHEMA,
    create_csv_content,
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
    manual_overrides: list[dict[str, str | None]] | None = None,
) -> dict[str, object]:
    return {
        "job_id": "test-job",
        "column_id": column_id,
        "column_name": column_name,
        "to_harmonize": original,
        "top_harmonization": harmonized,
        "ontology_id": None,
        "top_harmonizations": [harmonized] if harmonized else [],
        "confidence_score": 0.95,
        "error": None,
        "row_indices": [row_index],
        "manual_overrides": manual_overrides or [],
    }


def _save_pv_manifest(file_id: str, pvs_by_column_key: dict[str, frozenset[str]]) -> None:
    column_to_cde_key = {
        column_key: f"cde_{index}"
        for index, column_key in enumerate(pvs_by_column_key)
    }
    pvs = {
        cde_key: pvs_by_column_key[column_key]
        for column_key, cde_key in column_to_cde_key.items()
    }
    dependencies.get_workflow_storage().write_json(
        dependencies.get_user_context(),
        file_id,
        WorkflowFile.PV_MANIFEST,
        PVManifest(
            data_model_key=TEST_TARGET_SCHEMA,
            external_version_number="11.0.4",
            column_to_cde_key=ColumnCdeMap.from_strings(column_to_cde_key),
            pvs=CdePvCatalog.from_mapping(pvs),
        ).to_store(),
    )


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
    store_test_harmonization_manifest(temp_storage, file_id, manifest_rows)
    _save_pv_manifest(file_id, pvs_by_column_key)
    return file_id


async def test_non_conformant_endpoint_reports_zero_without_manifest(
    app_client: AsyncClient,
) -> None:
    """Missing manifest behaves as an empty non-conformant list."""

    # Given: a file has been uploaded but Stage 3 has not stored a manifest
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Bad Value"]]),
        "missing-manifest.csv",
    )
    assert dependencies.get_upload_storage().load_harmonization_manifest_path(file_id) is None

    # When: Stage 4 asks for the non-conformant values
    response = await app_client.get(f"/stage-4/non-conformant/{file_id}")

    # Then: the browser receives an empty result instead of an error
    assert response.status_code == 200
    assert response.json() == {"count": 0, "items": []}


async def test_non_conformant_endpoint_counts_current_unique_bad_values(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Stage 4 counts the current unique values that are outside their column PV set."""

    # Given: a manifest has unchanged, AI-changed, and manually-overridden bad values
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
            manual_overrides=[{
                "user_id": "reviewer",
                "timestamp": "2026-05-19T12:00:00+00:00",
                "value": "Bad Manual",
            }],
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

    # Then: only the three current bad values are shown
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 3
    assert data["items"] == [
        {"column": "diagnosis", "value": "Bad", "original": "Bad"},
        {"column": "diagnosis", "value": "Bad AI", "original": "Source"},
        {"column": "diagnosis", "value": "Bad Manual", "original": "Manual Source"},
    ]


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
        _manifest_row(column_id=0, column_name="diagnosis", original="Blank", harmonized="", row_index=1),
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
