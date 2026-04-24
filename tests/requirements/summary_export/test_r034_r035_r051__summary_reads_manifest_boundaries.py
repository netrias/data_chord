"""Requirement tests for summary behavior at manifest reader boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from src.domain.storage import UploadStorage
from tests.conftest import create_harmonized_csv, create_test_manifest_parquet, upload_content
from tests.requirements.helpers import CANONICAL_LUNG_CANCER, DIAGNOSIS_COLUMN, LOWERCASE_LUNG_CANCER

pytestmark = pytest.mark.asyncio


def _create_manifest_with_ai_whitespace(
    storage: UploadStorage,
    file_id: str,
    original_path: Path,
) -> None:
    manifest_rows = [{
        "job_id": f"test-job-{file_id}",
        "column_id": 0,
        "column_name": DIAGNOSIS_COLUMN,
        "to_harmonize": f" {LOWERCASE_LUNG_CANCER} ",
        "top_harmonization": f" {CANONICAL_LUNG_CANCER} ",
        "ontology_id": None,
        "top_harmonizations": [f" {CANONICAL_LUNG_CANCER} ", " Breast Cancer "],
        "confidence_score": 0.85,
        "error": None,
        "row_indices": [0],
        "manual_overrides": [],
    }]
    manifest_dir = storage.manifest_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    create_test_manifest_parquet(manifest_dir / f"{file_id}_harmonization.parquet", manifest_rows)
    create_harmonized_csv(original_path, {0: {DIAGNOSIS_COLUMN: CANONICAL_LUNG_CANCER}})


@pytest.mark.requirements("R-034", "R-035", "R-051")
async def test_r034_r035_r051__summary_strips_ai_output_but_not_original_user_value(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A workflow has manifest data with meaningful original whitespace and AI artifact whitespace.
    When: The user requests the Stage 5 summary.
    Then: The history shows stripped AI output and the unstripped original user value.
    """
    # Given
    original_with_whitespace = f" {LOWERCASE_LUNG_CANCER} "
    file_id = await upload_content(
        app_client,
        f"{DIAGNOSIS_COLUMN}\n{original_with_whitespace}\n".encode(),
        "ai-whitespace.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    _create_manifest_with_ai_whitespace(temp_storage, file_id, meta.saved_path)
    assert original_with_whitespace != LOWERCASE_LUNG_CANCER

    # When
    response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    term_mapping = response.json()["term_mappings"][0]
    history = term_mapping["history"]
    assert history[0]["source"] == "original"
    assert history[0]["value"] == original_with_whitespace
    assert history[1]["source"] == "ai"
    assert history[1]["value"] == CANONICAL_LUNG_CANCER
