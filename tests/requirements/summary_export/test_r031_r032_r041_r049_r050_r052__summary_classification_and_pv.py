"""Requirement tests for Stage 5 summary classification and exact PV behavior."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.domain.column_assignment import ColumnAssignment
from src.domain.data_model_cache import get_session_cache
from src.domain.storage import UploadStorage
from tests.conftest import create_harmonized_csv, create_test_manifest_parquet, upload_content
from tests.requirements.helpers import (
    CANONICAL_LUNG_CANCER,
    DIAGNOSIS_COLUMN,
    LOWERCASE_LUNG_CANCER,
    PRIMARY_DIAGNOSIS_CDE,
)

pytestmark = pytest.mark.asyncio


def _write_manifest(storage: UploadStorage, file_id: str, rows: list[dict[str, object]]) -> None:
    manifest_dir = storage.manifest_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    create_test_manifest_parquet(manifest_dir / f"{file_id}_harmonization.parquet", rows)


def _manifest_row(
    *,
    column_id: int,
    column_name: str,
    original: str,
    ai_value: str,
    row_index: int,
    manual_overrides: list[dict[str, str | None]] | None = None,
) -> dict[str, object]:
    return {
        "job_id": "requirements-summary",
        "column_id": column_id,
        "column_name": column_name,
        "to_harmonize": original,
        "top_harmonization": ai_value,
        "ontology_id": None,
        "top_harmonizations": [ai_value] if ai_value else [],
        "confidence_score": 0.85,
        "error": None,
        "row_indices": [row_index],
        "manual_overrides": manual_overrides or [],
    }


@pytest.mark.requirements("R-031", "R-032", "R-041")
async def test_r031_r032_r041__summary_counts_case_mismatch_as_non_conformant(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A harmonized value differs from the only permissible value by case.
    When: The user requests the Stage 5 summary.
    Then: The summary marks the value non-conformant by exact PV matching.
    """
    # Given
    file_id = await upload_content(app_client, f"{DIAGNOSIS_COLUMN}\n{LOWERCASE_LUNG_CANCER}\n".encode(), "case.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {0: {DIAGNOSIS_COLUMN: LOWERCASE_LUNG_CANCER}})
    _write_manifest(
        temp_storage,
        file_id,
        [
            _manifest_row(
                column_id=0,
                column_name=DIAGNOSIS_COLUMN,
                original=LOWERCASE_LUNG_CANCER,
                ai_value=LOWERCASE_LUNG_CANCER,
                row_index=0,
            )
        ],
    )
    cache = get_session_cache(file_id)
    cache.set_column_assignments({0: ColumnAssignment(0, DIAGNOSIS_COLUMN, PRIMARY_DIAGNOSIS_CDE, "harmonizable")})
    cache.set_pvs(PRIMARY_DIAGNOSIS_CDE, frozenset([CANONICAL_LUNG_CANCER]))
    pvs = cache.get_pvs_for_column(0)
    assert pvs is not None
    assert LOWERCASE_LUNG_CANCER not in pvs

    # When
    response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    body = response.json()
    assert body["non_conformant_count"] == 1
    assert body["term_mappings"][0]["is_pv_conformant"] is False


@pytest.mark.requirements("R-049", "R-052")
async def test_r049_r052__summary_classifies_changes_and_collapses_duplicate_override_history(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A manifest contains unchanged, AI-harmonized, and repeated manual override transformations.
    When: The user requests the Stage 5 summary.
    Then: The summary classifies each change type and collapses consecutive duplicate override history.
    """
    # Given
    file_id = await upload_content(app_client, b"diagnosis\nOriginal\nSame\nManual\n", "history.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {0: {DIAGNOSIS_COLUMN: "AI Value"}, 2: {DIAGNOSIS_COLUMN: "Final Value"}})
    _write_manifest(
        temp_storage,
        file_id,
        [
            _manifest_row(
                column_id=0, column_name=DIAGNOSIS_COLUMN, original="Original", ai_value="AI Value", row_index=0
            ),
            _manifest_row(column_id=0, column_name=DIAGNOSIS_COLUMN, original="Same", ai_value="Same", row_index=1),
            _manifest_row(
                column_id=0,
                column_name=DIAGNOSIS_COLUMN,
                original="Manual",
                ai_value="AI Before User",
                row_index=2,
                manual_overrides=[
                    {"user_id": "u", "timestamp": "2024-01-01T00:00:00Z", "value": "Final Value"},
                    {"user_id": "u", "timestamp": "2024-01-01T00:00:01Z", "value": "Final Value"},
                ],
            ),
        ],
    )

    # When
    response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    summary = response.json()["column_summaries"][0]
    assert summary["ai_changes"] == 1
    assert summary["manual_changes"] == 1
    assert summary["unchanged"] == 1
    manual_mapping = next(m for m in response.json()["term_mappings"] if m["original_value"] == "Manual")
    assert [step["source"] for step in manual_mapping["history"]].count("user") == 1


@pytest.mark.requirements("R-050")
async def test_r050__summary_preserves_duplicate_named_columns_by_column_id(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A manifest has two duplicate-named columns with different column ids.
    When: The user requests the Stage 5 summary.
    Then: The summary preserves both mappings as distinct entries.
    """
    # Given
    file_id = await upload_content(
        app_client,
        f"{DIAGNOSIS_COLUMN},{DIAGNOSIS_COLUMN}\nA,B\n".encode(),
        "duplicate-summary.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})
    _write_manifest(
        temp_storage,
        file_id,
        [
            _manifest_row(column_id=0, column_name=DIAGNOSIS_COLUMN, original="Shared", ai_value="Shared", row_index=0),
            _manifest_row(column_id=1, column_name=DIAGNOSIS_COLUMN, original="Shared", ai_value="Shared", row_index=0),
        ],
    )

    # When
    response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    assert len(response.json()["term_mappings"]) == 2
