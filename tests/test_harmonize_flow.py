"""Feature tests for Stage 3 harmonization dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from httpx import AsyncClient

from src.domain.manifest import ManifestPayload
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


async def test_harmonize_uses_stored_mapping_manifest_when_request_omits_manifest(
    app_client: AsyncClient,
    sample_csv_path: Path,
    mock_netrias_client: MagicMock,
) -> None:
    """Stage 3 can harmonize from the manifest saved by Stage 1 analysis."""

    # Given: Stage 1 has analyzed a file and saved its mapping manifest server-side
    file_id = await upload_and_analyze(app_client, sample_csv_path)
    assert not mock_netrias_client.harmonize.called

    # When: the browser triggers harmonization without carrying the manifest body
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
        },
    )

    # Then: harmonization uses the stored column-keyed manifest
    assert response.status_code == 200
    sdk_manifest = mock_netrias_client.harmonize.call_args.kwargs["manifest"]
    assert sdk_manifest["column_mappings"]["col_0000"]["cde_key"] == "primary_diagnosis"
    assert sdk_manifest["column_mappings"]["col_0001"]["cde_key"] == "therapeutic_agents"


async def test_harmonize_prefers_stored_mapping_manifest_over_stale_request_manifest(
    app_client: AsyncClient,
    sample_csv_path: Path,
    mock_netrias_client: MagicMock,
) -> None:
    """The durable analysis result is the backend source of truth for mappings."""

    # Given: Stage 1 has saved the current mapping manifest, and the request carries stale browser data
    file_id = await upload_and_analyze(app_client, sample_csv_path)
    stale_manifest: ManifestPayload = {
        "column_mappings": {
            "col_0000": {"column_name": "primary_diagnosis", "cde_key": "therapeutic_agents", "cde_id": 1},
        },
    }
    assert not mock_netrias_client.harmonize.called

    # When: harmonization is triggered with the stale manifest still present in the request
    response = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "target_schema": TEST_TARGET_SCHEMA,
            "manual_overrides": {},
            "manifest": stale_manifest,
        },
    )

    # Then: Stage 3 ignores the stale copy and uses the manifest saved after analysis
    assert response.status_code == 200
    sdk_manifest = mock_netrias_client.harmonize.call_args.kwargs["manifest"]
    assert sdk_manifest["column_mappings"]["col_0000"]["cde_key"] == "primary_diagnosis"
    assert sdk_manifest["column_mappings"]["col_0001"]["cde_key"] == "therapeutic_agents"


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

    from src.domain import ColumnCdeOverrides, ColumnRenameSet
    from src.domain.harmonize import HarmonizeService, HarmonizeStatus

    service = HarmonizeService(client=None)

    # When: Harmonization is attempted without the client
    from src.domain.data_model_cache import SessionCache

    result = service.run(
        file_path=Path("/tmp/test.csv"),
        target_schema=TEST_TARGET_SCHEMA,
        column_overrides=ColumnCdeOverrides.from_strings({}),
        column_renames=ColumnRenameSet.empty(),
        cache=SessionCache(),
        manifest=None,
    )

    # Then: Returns a stubbed/queued job indicating service unavailability
    assert result.status == HarmonizeStatus.QUEUED
    assert "stubbed" in result.detail.lower() or "unavailable" in result.detail.lower()


def test_harmonize_sends_source_file_and_column_keyed_manifest(tmp_path: Path) -> None:
    """Harmonize lets the SDK handle tabular format details directly."""

    # Given: a duplicate-header CSV and a manifest keyed by source column key
    from src.domain import ColumnCdeOverrides, ColumnRenameSet
    from src.domain.data_model_cache import SessionCache
    from src.domain.harmonize import HarmonizeService

    csv_path = tmp_path / "dupes.csv"
    csv_path.write_text("name,name\nAlice,Smith\n", encoding="utf-8")
    mock_client = MagicMock()
    mock_client.harmonize.return_value = MagicMock(status="succeeded", description="ok", job_id="job-1")
    service = HarmonizeService(mock_client)
    manifest: ManifestPayload = {"column_mappings": {"col_0001": {"cde_key": "last_name", "cde_id": 11}}}

    # When: harmonization is run
    result = service.run(
        file_path=csv_path,
        target_schema=TEST_TARGET_SCHEMA,
        column_overrides=ColumnCdeOverrides.from_strings({}),
        column_renames=ColumnRenameSet.empty(),
        cache=SessionCache(),
        manifest=manifest,
    )

    # Then: the SDK sees the original file and column-keyed manifest directly
    assert result.job_id == "job-1"
    harmonize_kwargs = mock_client.harmonize.call_args.kwargs
    sdk_manifest = harmonize_kwargs["manifest"]
    sdk_keys = list(sdk_manifest["column_mappings"].keys())
    assert sdk_keys == ["col_0001"]
    assert sdk_manifest["column_mappings"]["col_0001"]["alternatives"] == []
    assert harmonize_kwargs["source_path"].name == "dupes.csv"


def test_harmonize_applies_column_renames_to_manifest(tmp_path: Path) -> None:
    """
    Given: Stage 2 submits a column rename for an existing mapped column
    When: harmonization is run
    Then: the SDK manifest uses the renamed column_name while keeping the column key stable
    """
    from src.domain import ColumnCdeOverrides, ColumnRenameSet
    from src.domain.data_model_cache import SessionCache
    from src.domain.harmonize import HarmonizeService

    # Given
    csv_path = tmp_path / "source.csv"
    csv_path.write_text("diagnosis\nLung\n", encoding="utf-8")
    mock_client = MagicMock()
    mock_client.harmonize.return_value = MagicMock(status="succeeded", description="ok", job_id="job-1")
    service = HarmonizeService(mock_client)
    manifest: ManifestPayload = {
        "column_mappings": {
            "col_0000": {"column_name": "diagnosis", "cde_key": "primary_diagnosis", "cde_id": 11}
        }
    }
    assert manifest["column_mappings"]["col_0000"].get("column_name") == "diagnosis"

    # When
    result = service.run(
        file_path=csv_path,
        target_schema=TEST_TARGET_SCHEMA,
        column_overrides=ColumnCdeOverrides.from_strings({}),
        column_renames=ColumnRenameSet.from_dict({"col_0000": "Primary Diagnosis"}),
        cache=SessionCache(),
        manifest=manifest,
    )

    # Then
    assert result.job_id == "job-1"
    sdk_manifest = mock_client.harmonize.call_args.kwargs["manifest"]
    assert list(sdk_manifest["column_mappings"].keys()) == ["col_0000"]
    assert sdk_manifest["column_mappings"]["col_0000"]["column_name"] == "Primary Diagnosis"
