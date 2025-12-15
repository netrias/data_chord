"""Feature tests for Stage 3 harmonization dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from httpx import AsyncClient

from src.domain import CDEField, normalize_target_name
from tests.conftest import TEST_TARGET_SCHEMA, upload_and_analyze


async def test_harmonize_returns_job_id(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Harmonize endpoint returns a job_id for tracking."""

    # Given: An uploaded and analyzed CSV file
    file_id = await upload_and_analyze(app_client, sample_csv_path)

    # When: Harmonization is triggered
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
        },
    )

    # Then: Response contains a job_id for tracking progress
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert len(data["job_id"]) > 0


async def test_harmonize_returns_status(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Harmonize endpoint returns execution status."""

    # Given: An uploaded and analyzed CSV file
    file_id = await upload_and_analyze(app_client, sample_csv_path)

    # When: Harmonization is triggered
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
        },
    )

    # Then: Response contains status indicating success
    data = response.json()
    assert "status" in data
    assert data["status"] == "succeeded"


async def test_harmonize_with_manual_overrides(
    app_client: AsyncClient,
    sample_csv_path: Path,
    mock_netrias_client: MagicMock,
) -> None:
    """Manual overrides are passed to the harmonize service."""

    # Given: An uploaded and analyzed CSV file with manual column overrides
    file_id = await upload_and_analyze(app_client, sample_csv_path)
    overrides = {"primary_diagnosis": "primary_diagnosis"}

    # When: Harmonization is triggered with manual overrides
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": overrides,
        },
    )

    # Then: Harmonization succeeds with the manual overrides applied
    assert response.status_code == 200


async def test_harmonize_file_not_found(app_client: AsyncClient) -> None:
    """Harmonize with non-existent file_id returns 404."""

    # Given: A file_id that does not exist in storage
    invalid_file_id = "deadbeef12345678deadbeef12345678"

    # When: Harmonization is triggered with invalid file_id
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": invalid_file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
        },
    )

    # Then: 404 Not Found response
    assert response.status_code == 404


async def test_harmonize_returns_next_stage_url(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Harmonize response includes URL for next stage."""

    # Given: An uploaded and analyzed CSV file
    file_id = await upload_and_analyze(app_client, sample_csv_path)

    # When: Harmonization is triggered
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
        },
    )

    # Then: Response contains URL to stage 4 (review)
    data = response.json()
    assert "next_stage_url" in data
    assert "/stage-4" in data["next_stage_url"]


def test_target_alias_resolution_primary_diagnosis() -> None:
    """Target alias 'Primary Diagnosis' normalizes to CDEField.PRIMARY_DIAGNOSIS."""

    # Given: A human-readable target name with spaces and title case
    input_value = "Primary Diagnosis"

    # When: The target name is normalized
    result = normalize_target_name(input_value)

    # Then: Returns the corresponding CDEField enum
    assert result == CDEField.PRIMARY_DIAGNOSIS


def test_target_alias_resolution_therapeutic_agents() -> None:
    """Target alias 'Therapeutic Agents' normalizes to CDEField.THERAPEUTIC_AGENTS."""

    # Given: A human-readable target name with spaces and title case
    input_value = "Therapeutic Agents"

    # When: The target name is normalized
    result = normalize_target_name(input_value)

    # Then: Returns the corresponding CDEField enum
    assert result == CDEField.THERAPEUTIC_AGENTS


def test_target_alias_resolution_sample_anatomic_site() -> None:
    """Target alias 'Sample Anatomic Site' normalizes to CDEField.SAMPLE_ANATOMIC_SITE."""

    # Given: A human-readable target name with spaces and title case
    input_value = "Sample Anatomic Site"

    # When: The target name is normalized
    result = normalize_target_name(input_value)

    # Then: Returns the corresponding CDEField enum
    assert result == CDEField.SAMPLE_ANATOMIC_SITE


def test_target_alias_resolution_tissue_or_organ_of_origin() -> None:
    """Target alias 'Tissue or Organ of Origin' normalizes to CDEField.TISSUE_OR_ORGAN_OF_ORIGIN."""

    # Given: A human-readable target name with spaces and title case
    input_value = "Tissue or Organ of Origin"

    # When: The target name is normalized
    result = normalize_target_name(input_value)

    # Then: Returns the corresponding CDEField enum
    assert result == CDEField.TISSUE_OR_ORGAN_OF_ORIGIN


def test_target_alias_resolution_already_normalized() -> None:
    """Already normalized target names return the CDEField enum."""

    # Given: A snake_case target name (already normalized)
    input_value = "morphology"

    # When: The target name is normalized
    result = normalize_target_name(input_value)

    # Then: Returns the corresponding CDEField enum
    assert result == CDEField.MORPHOLOGY


def test_target_alias_resolution_empty_returns_none() -> None:
    """Empty or None input returns None."""

    # Given: Empty, None, or whitespace-only input
    # When: The target name is normalized
    # Then: Returns None for invalid inputs
    assert normalize_target_name("") is None
    assert normalize_target_name(None) is None
    assert normalize_target_name("   ") is None


async def test_harmonize_without_client_returns_stubbed_job(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """When Netrias client is unavailable, return a stubbed job."""

    # Given: An uploaded file, but Netrias client is unavailable
    _ = await upload_and_analyze(app_client, sample_csv_path)

    with patch("src.stage_1_upload.harmonize.HarmonizeService._build_client", return_value=None):
        from src.stage_1_upload.harmonize import HarmonizeService

        service = HarmonizeService()
        service._client = None

        # When: Harmonization is attempted without the client
        result = service.run(
            file_path=Path("/tmp/test.csv"),
            target_schema=TEST_TARGET_SCHEMA,
            manual_overrides={},
            manifest=None,
        )

        # Then: Returns a stubbed/queued job indicating service unavailability
        assert result.status == "queued"
        assert "stubbed" in result.detail.lower() or "unavailable" in result.detail.lower()
