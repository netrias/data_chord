"""Feature tests for Stage 3 harmonization dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from httpx import AsyncClient

from src.stage_1_upload.harmonize import _normalize_target_name


async def _upload_and_analyze(client: AsyncClient, csv_path: Path) -> str:
    """why: helper to upload and analyze a file, returning file_id."""
    upload_response = await client.post(
        "/stage-1/upload",
        files={"file": (csv_path.name, csv_path.read_bytes(), "text/csv")},
    )
    file_id = upload_response.json()["file_id"]

    await client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )
    return file_id


async def test_harmonize_returns_job_id(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Harmonize endpoint returns a job_id for tracking."""

    # Given
    file_id = await _upload_and_analyze(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": "CCDI",
            "manual_overrides": {},
        },
    )

    # Then
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert len(data["job_id"]) > 0


async def test_harmonize_returns_status(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Harmonize endpoint returns execution status."""

    # Given
    file_id = await _upload_and_analyze(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": "CCDI",
            "manual_overrides": {},
        },
    )

    # Then
    data = response.json()
    assert "status" in data
    assert data["status"] == "succeeded"


async def test_harmonize_with_manual_overrides(
    app_client: AsyncClient,
    sample_csv_path: Path,
    mock_netrias_client: MagicMock,
) -> None:
    """Manual overrides are passed to the harmonize service."""

    # Given
    file_id = await _upload_and_analyze(app_client, sample_csv_path)
    overrides = {"primary_diagnosis": "primary_diagnosis"}

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": "CCDI",
            "manual_overrides": overrides,
        },
    )

    # Then
    assert response.status_code == 200


async def test_harmonize_file_not_found(app_client: AsyncClient) -> None:
    """Harmonize with non-existent file_id returns 404."""

    # Given
    invalid_file_id = "deadbeef12345678deadbeef12345678"

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": invalid_file_id,
            "target_schema": "CCDI",
            "manual_overrides": {},
        },
    )

    # Then
    assert response.status_code == 404


async def test_harmonize_returns_next_stage_url(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Harmonize response includes URL for next stage."""

    # Given
    file_id = await _upload_and_analyze(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": "CCDI",
            "manual_overrides": {},
        },
    )

    # Then
    data = response.json()
    assert "next_stage_url" in data
    assert "/stage-4" in data["next_stage_url"]


def test_target_alias_resolution_primary_diagnosis() -> None:
    """Target alias 'Primary Diagnosis' normalizes to 'primary_diagnosis'."""

    # Given
    input_value = "Primary Diagnosis"

    # When
    result = _normalize_target_name(input_value)

    # Then
    assert result == "primary_diagnosis"


def test_target_alias_resolution_therapeutic_agents() -> None:
    """Target alias 'Therapeutic Agents' normalizes to 'therapeutic_agents'."""

    # Given
    input_value = "Therapeutic Agents"

    # When
    result = _normalize_target_name(input_value)

    # Then
    assert result == "therapeutic_agents"


def test_target_alias_resolution_sample_anatomic_site() -> None:
    """Target alias 'Sample Anatomic Site' normalizes to 'sample_anatomic_site'."""

    # Given
    input_value = "Sample Anatomic Site"

    # When
    result = _normalize_target_name(input_value)

    # Then
    assert result == "sample_anatomic_site"


def test_target_alias_resolution_tissue_or_organ_of_origin() -> None:
    """Target alias 'Tissue or Organ of Origin' normalizes correctly."""

    # Given
    input_value = "Tissue or Organ of Origin"

    # When
    result = _normalize_target_name(input_value)

    # Then
    assert result == "tissue_or_organ_of_origin"


def test_target_alias_resolution_already_normalized() -> None:
    """Already normalized target names pass through unchanged."""

    # Given
    input_value = "morphology"

    # When
    result = _normalize_target_name(input_value)

    # Then
    assert result == "morphology"


def test_target_alias_resolution_empty_returns_none() -> None:
    """Empty or None input returns None."""

    # Given / When / Then
    assert _normalize_target_name("") is None
    assert _normalize_target_name(None) is None
    assert _normalize_target_name("   ") is None


async def test_harmonize_without_client_returns_stubbed_job(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """When Netrias client is unavailable, return a stubbed job."""

    # Given
    _ = await _upload_and_analyze(app_client, sample_csv_path)

    with patch("src.stage_1_upload.harmonize.HarmonizeService._build_client", return_value=None):
        from src.stage_1_upload.harmonize import HarmonizeService

        service = HarmonizeService()
        service._client = None

        # When
        result = service.run(
            file_path=Path("/tmp/test.csv"),
            target_schema="CCDI",
            manual_overrides={},
            manifest=None,
        )

        # Then
        assert result.status == "queued"
        assert "stubbed" in result.detail.lower() or "unavailable" in result.detail.lower()
