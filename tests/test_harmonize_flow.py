"""Feature tests for Stage 3 harmonization dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from httpx import AsyncClient

from src.domain.harmonization import HarmonizeStatus
from tests.conftest import (
    TEST_TARGET_EXTERNAL_VERSION_NUMBER,
    TEST_TARGET_SCHEMA,
    confirm_mapping_choices,
    upload_and_analyze,
)


def test_harmonize_status_values_remain_stable() -> None:
    # Given durable status values, when listed, then their stored strings stay stable.
    assert {status.value for status in HarmonizeStatus} == {"queued", "succeeded", "failed"}


async def test_harmonize_uses_confirmed_mapping_and_exact_reference_values(
    app_client: AsyncClient,
    sample_csv_path: Path,
    mock_netrias_client: MagicMock,
) -> None:
    # Given an analyzed file with one confirmed mapping override.
    file_id = await upload_and_analyze(app_client, sample_csv_path)
    await confirm_mapping_choices(
        app_client,
        file_id,
        manual_overrides={"col_0000": "primary_diagnosis"},
    )

    # When Stage 3 runs.
    response = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    data = response.json()
    for _ in range(100):
        if data["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.01)
        data = (
            await app_client.get(
                f"/stage-3/jobs/{data['job_id']}",
                params={"file_id": file_id},
            )
        ).json()

    # Then the agentic service gets the confirmed CDE and its exact value set.
    assert response.status_code == 200
    assert data["status"] == "succeeded"
    arguments = mock_netrias_client.run.call_args.kwargs
    assert arguments["data_model_version"].data_model_key == TEST_TARGET_SCHEMA
    assert (
        arguments["data_model_version"].external_version_number
        == TEST_TARGET_EXTERNAL_VERSION_NUMBER
    )
    record = arguments["prepared_manifest"].records["col_0000"]
    assert record.cde_key == "primary_diagnosis"
    assert arguments["column_pv_sets"].get("col_0000") == frozenset(
        {"Lung Cancer", "Breast Cancer", "Diabetes", "Hypertension"}
    )


async def test_harmonize_missing_workflow_returns_404(app_client: AsyncClient) -> None:
    # Given an unknown workflow, when Stage 3 starts, then it returns 404.
    response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": "deadbeef12345678deadbeef12345678"},
    )
    assert response.status_code == 404


async def test_harmonize_returns_the_next_stage_url(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    # Given an analyzed file, when Stage 3 starts, then the response points to Stage 4.
    file_id = await upload_and_analyze(app_client, sample_csv_path)
    response = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert response.status_code == 200
    assert "/stage-4" in response.json()["next_stage_url"]
