"""Feature tests for Stage 3 harmonization dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from src.domain.harmonization import HarmonizeStatus
from src.domain.manifest import ColumnMappingManifest, ManifestPayload
from src.integrations.netrias_harmonize import HarmonizeService
from tests.conftest import TEST_TARGET_SCHEMA, MockHarmonizeResult, upload_and_analyze


def test_harmonize_status_values_remain_stable() -> None:
    """Durable job state and API responses use these exact values."""
    assert {status.value for status in HarmonizeStatus} == {
        "queued",
        "succeeded",
        "failed",
    }


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
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
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
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
                "manual_overrides": {},
        },
    )

    # Then: Response contains status indicating success
    data = response.json()
    assert "status" in data
    for _ in range(100):
        if data["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.01)
        poll_response = await app_client.get(
            f"/stage-3/jobs/{data['job_id']}",
            params={"file_id": file_id},
        )
        assert poll_response.status_code == 200
        data = poll_response.json()
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
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
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
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
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
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
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
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
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
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
                "manual_overrides": {},
        },
    )

    # Then: Response contains URL to stage 4 (review)
    data = response.json()
    assert "next_stage_url" in data
    assert "/stage-4" in data["next_stage_url"]


async def test_failed_retry_does_not_reopen_previous_successful_artifacts(
    app_client: AsyncClient,
    sample_csv_path: Path,
    mock_netrias_client: MagicMock,
) -> None:
    """Later stages stay closed when the newest run fails but old files remain."""
    file_id = await upload_and_analyze(app_client, sample_csv_path)
    first = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert first.status_code == 200
    first_job = first.json()
    for _ in range(100):
        if first_job["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.01)
        first_job = (
            await app_client.get(
                f"/stage-3/jobs/{first_job['job_id']}",
                params={"file_id": file_id},
            )
        ).json()
    assert first_job["status"] == "succeeded"
    assert (await app_client.post("/stage-4/rows", json={"file_id": file_id})).status_code == 200

    mock_netrias_client.harmonize.side_effect = None
    mock_netrias_client.harmonize.return_value = MockHarmonizeResult(
        status="failed",
        description="provider secret",
        job_id="failed-job",
    )
    failed = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})

    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert (await app_client.post("/stage-4/rows", json={"file_id": file_id})).status_code == 404
    assert (await app_client.post("/stage-5/download", json={"file_id": file_id})).status_code == 404


def test_harmonize_requires_a_provider_client() -> None:
    """A caller learns during setup when the provider is unavailable."""

    with pytest.raises(RuntimeError, match="NetriasClient unavailable"):
        HarmonizeService(client=None)


def test_harmonize_sends_the_prepared_manifest_to_the_provider(tmp_path: Path) -> None:
    """The provider receives the caller's complete, typed harmonization plan."""

    # Given: a duplicate-header file and a manifest already prepared by the workflow
    csv_path = tmp_path / "dupes.csv"
    csv_path.write_text("name,name\nAlice,Smith\n", encoding="utf-8")
    requested_output_path = tmp_path / "requested-output.csv"
    provider_manifest_path = tmp_path / "provider-manifest.parquet"
    provider_manifest_path.touch()
    requested_output_path.touch()
    mock_client = MagicMock()
    mock_client.harmonize.return_value = MagicMock(
        status="succeeded",
        description="ok",
        job_id="job-1",
        manifest_path=provider_manifest_path,
        file_path=requested_output_path,
    )
    service = HarmonizeService(mock_client)
    prepared_manifest = ColumnMappingManifest.from_payload_strict(
        {
            "column_mappings": {
                "col_0001": {
                    "column_name": "Family Name",
                    "cde_key": "last_name",
                    "cde_id": 11,
                }
            }
        }
    )

    # When: harmonization is run
    result = service.run(
        file_path=csv_path,
        data_model_key=TEST_TARGET_SCHEMA,
        external_version_number="11.0.4",
        prepared_manifest=prepared_manifest,
        output_path=requested_output_path,
        sheet_name="Patients",
    )

    # Then: the SDK receives one complete provider request and its artifacts are surfaced
    assert result.job_id == "job-1"
    assert result.status == HarmonizeStatus.SUCCEEDED
    assert result.job_id_available is True
    assert result.manifest_path == provider_manifest_path
    assert result.output_path == requested_output_path
    assert mock_client.harmonize.call_args.kwargs == {
        "source_path": csv_path,
        "manifest": {
            "column_mappings": {
                "col_0001": {
                    "column_name": "Family Name",
                    "cde_key": "last_name",
                    "cde_id": 11,
                    "harmonization": "harmonizable",
                    "alternatives": [],
                }
            }
        },
        "target_schema": TEST_TARGET_SCHEMA,
        "external_version_number": "11.0.4",
        "output_path": requested_output_path,
        "sheet_name": "Patients",
    }


def test_harmonize_hides_provider_exception_details(tmp_path: Path) -> None:
    """Provider failures give callers a safe failure instead of leaking internals."""

    csv_path = tmp_path / "source.csv"
    csv_path.write_text("diagnosis\nLung\n", encoding="utf-8")
    mock_client = MagicMock()
    mock_client.harmonize.side_effect = RuntimeError("secret provider URL and token")
    service = HarmonizeService(mock_client)
    prepared_manifest = ColumnMappingManifest.empty()

    result = service.run(
        file_path=csv_path,
        data_model_key=TEST_TARGET_SCHEMA,
        external_version_number="11.0.4",
        prepared_manifest=prepared_manifest,
    )

    assert result.status == HarmonizeStatus.FAILED
    assert result.detail == "Harmonization provider failed."
    assert "secret" not in result.detail
    assert result.job_id_available is False


@pytest.mark.parametrize("provider_status", [None, "unexpected"])
def test_harmonize_rejects_invalid_provider_status(
    provider_status: str | None,
    tmp_path: Path,
) -> None:
    """Missing and unknown provider statuses cannot be reported as success."""

    csv_path = tmp_path / "source.csv"
    csv_path.write_text("diagnosis\nLung\n", encoding="utf-8")
    mock_client = MagicMock()
    mock_client.harmonize.return_value = MagicMock(status=provider_status)
    service = HarmonizeService(mock_client)

    result = service.run(
        file_path=csv_path,
        data_model_key=TEST_TARGET_SCHEMA,
        external_version_number="11.0.4",
        prepared_manifest=ColumnMappingManifest.empty(),
    )

    assert result.status == HarmonizeStatus.FAILED
    assert result.detail == "Harmonization provider failed."
    assert result.job_id_available is False
