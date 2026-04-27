"""Feature tests for Stage 3 harmonization dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from httpx import AsyncClient

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


async def test_harmonize_without_client_returns_stubbed_job(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """When Netrias client is unavailable, return a stubbed job."""

    # Given: An uploaded file, but Netrias client is unavailable
    _ = await upload_and_analyze(app_client, sample_csv_path)

    from src.domain import ColumnMappingSet
    from src.domain.harmonize import HarmonizeService, HarmonizeStatus

    service = HarmonizeService(client=None)

    # When: Harmonization is attempted without the client
    from src.domain.data_model_cache import SessionCache

    result = service.run(
        file_path=Path("/tmp/test.csv"),
        target_schema=TEST_TARGET_SCHEMA,
        column_mappings=ColumnMappingSet.from_dict({}),
        cache=SessionCache(),
        manifest=None,
    )

    # Then: Returns a stubbed/queued job indicating service unavailability
    assert result.status == HarmonizeStatus.QUEUED
    assert "stubbed" in result.detail.lower() or "unavailable" in result.detail.lower()


def test_harmonize_sends_source_file_and_column_keyed_manifest(tmp_path: Path) -> None:
    """Harmonize lets the SDK handle tabular format details directly."""

    # Given: a duplicate-header CSV and a manifest keyed by source column key
    from src.domain import ColumnMappingSet
    from src.domain.data_model_cache import SessionCache
    from src.domain.harmonize import HarmonizeService

    csv_path = tmp_path / "dupes.csv"
    csv_path.write_text("name,name\nAlice,Smith\n", encoding="utf-8")
    mock_client = MagicMock()
    mock_client.harmonize.return_value = MagicMock(status="succeeded", description="ok", job_id="job-1")
    service = HarmonizeService(mock_client)
    manifest = {"column_mappings": {"col_0001": {"targetField": "last_name", "cde_id": 11}}}

    # When: harmonization is run
    result = service.run(
        file_path=csv_path,
        target_schema=TEST_TARGET_SCHEMA,
        column_mappings=ColumnMappingSet.from_dict({}),
        cache=SessionCache(),
        manifest=manifest,
    )

    # Then: the SDK sees the original file and column-keyed manifest directly
    assert result.job_id == "job-1"
    harmonize_kwargs = mock_client.harmonize.call_args.kwargs
    sdk_manifest = harmonize_kwargs["manifest"]
    sdk_keys = list(sdk_manifest["column_mappings"].keys())
    assert sdk_keys == ["col_0001"]
    assert harmonize_kwargs["source_path"].name == "dupes.csv"
