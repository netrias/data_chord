"""Requirement tests for PV adjustments through Stage 3 and Stage 5."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from tests.conftest import (
    TEST_TARGET_SCHEMA,
    MockHarmonizeResult,
    create_test_manifest_parquet,
    upload_content,
)
from tests.requirements.helpers import (
    CANONICAL_LUNG_CANCER,
    DIAGNOSIS_COLUMN,
    LOWERCASE_LUNG_CANCER,
    harmonizer_manifest_row,
    load_reference_json,
)

pytestmark = pytest.mark.asyncio


def _write_harmonizer_manifest(
    path: Path,
    *,
    original: str,
    ai_value: str,
    alternatives: list[str],
) -> None:
    create_test_manifest_parquet(
        path,
        [
            harmonizer_manifest_row(
                original=original,
                ai_value=ai_value,
                alternatives=alternatives,
                job_id="pv-adjustment",
            )
        ],
    )


async def _run_stage_three_with_manifest(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    manifest_path: Path,
    pv_set: frozenset[str],
    original: str,
) -> str:
    file_id = await upload_content(app_client, f"{DIAGNOSIS_COLUMN}\n{original}\n".encode(), "pv-adjust.csv")
    stage_two = await app_client.get(f"/stage-2?file_id={file_id}&schema={TEST_TARGET_SCHEMA}")
    assert stage_two.status_code == 200
    mock_netrias_client.harmonize.return_value = MockHarmonizeResult(
        status="succeeded",
        description="Harmonization completed.",
        job_id="pv-adjustment-job",
        manifest_path=manifest_path,
    )
    mock_netrias_client.get_pv_set_async = AsyncMock(return_value=pv_set)

    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
            "manifest": load_reference_json("stage3_primary_diagnosis_manifest.json"),
        },
    )
    assert response.status_code == 200
    return file_id


@pytest.mark.requirements("R-028", "R-033", "R-064")
async def test_r028_r033_r064__stage3_preserves_valid_original_pv_for_stage5_summary(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    tmp_path: Path,
) -> None:
    """
    Given: The harmonizer returns a different AI value while the user's original value is already a valid PV.
    When: The user runs Stage 3 and then requests the Stage 5 summary.
    Then: The persisted manifest used by Stage 5 preserves the original value as final.
    """
    # Given
    manifest_path = tmp_path / "original-valid.parquet"
    _write_harmonizer_manifest(
        manifest_path,
        original=LOWERCASE_LUNG_CANCER,
        ai_value=CANONICAL_LUNG_CANCER,
        alternatives=[CANONICAL_LUNG_CANCER],
    )
    assert LOWERCASE_LUNG_CANCER != CANONICAL_LUNG_CANCER

    # When
    file_id = await _run_stage_three_with_manifest(
        app_client,
        mock_netrias_client,
        manifest_path,
        frozenset([LOWERCASE_LUNG_CANCER, CANONICAL_LUNG_CANCER]),
        LOWERCASE_LUNG_CANCER,
    )
    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then
    assert summary_response.status_code == 200
    term_mapping = summary_response.json()["term_mappings"][0]
    assert term_mapping["original_value"] == LOWERCASE_LUNG_CANCER
    assert term_mapping["final_value"] == LOWERCASE_LUNG_CANCER


@pytest.mark.requirements("R-042")
async def test_r042__stage3_uses_first_conformant_alternative_for_stage5_summary(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    tmp_path: Path,
) -> None:
    """
    Given: The original and AI values are not valid PVs but a later alternative is valid.
    When: The user runs Stage 3 and then requests the Stage 5 summary.
    Then: The persisted manifest used by Stage 5 contains the first conformant alternative.
    """
    # Given
    manifest_path = tmp_path / "alternative-valid.parquet"
    invalid_ai_value = "LUNG CANCER"
    misspelled_original = "lung canser"
    _write_harmonizer_manifest(
        manifest_path,
        original=misspelled_original,
        ai_value=invalid_ai_value,
        alternatives=[invalid_ai_value, CANONICAL_LUNG_CANCER],
    )
    assert invalid_ai_value != CANONICAL_LUNG_CANCER

    # When
    file_id = await _run_stage_three_with_manifest(
        app_client,
        mock_netrias_client,
        manifest_path,
        frozenset([CANONICAL_LUNG_CANCER]),
        misspelled_original,
    )
    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then
    assert summary_response.status_code == 200
    assert summary_response.json()["term_mappings"][0]["final_value"] == CANONICAL_LUNG_CANCER
