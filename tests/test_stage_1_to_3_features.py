"""Stage 1 through Stage 3 feature tests."""

from __future__ import annotations

import asyncio
import csv
import io
import shutil
import time
import zipfile
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
from src.domain.cde_recommendation import RecommendationUnavailableError
from src.domain.columns import column_key_from_string
from src.domain.harmonization import HarmonizeStatus, MatchFidelity
from src.domain.harmonization_cache import EmptyHarmonizationCache
from src.domain.manifest import ManifestPayload
from src.domain.reference_data import ReferenceDataUnavailableError, ReferenceModelNotFoundError
from src.domain.workflow_state import WorkflowState
from src.integrations.harmonize import (
    FileHarmonizationService,
    HarmonizeResult,
    TermHarmonizationRequest,
    TermHarmonizationResponse,
)
from src.persistence.harmonization_job_store import (
    HarmonizationJobState,
    LoadedHarmonizationJob,
    load_harmonization_job,
    save_harmonization_job,
)
from src.persistence.workflow_artifacts import open_upload_artifact
from src.persistence.workflow_state_store import load_workflow_state
from src.storage import S3WorkflowStorage, UploadConstraints, UploadStorage, WorkflowFile
from tests.conftest import (
    TEST_TARGET_SCHEMA,
    TEST_TSV_CONTENT_TYPE,
    TEST_XLSX_CONTENT_TYPE,
    confirm_mapping_choices,
    create_csv_content,
    create_test_manifest_parquet,
    create_xlsx_content,
    review_precondition,
    review_state_payload,
    upload_content,
    upload_integrity_fields,
)
from tests.test_s3_workflow_storage import FakeS3Client

GENERIC_API_ERROR_DETAIL = "We couldn't process this request. Please try again."

pytestmark = pytest.mark.asyncio


def _load_workflow_state(file_id: str) -> WorkflowState | None:
    loaded = load_workflow_state(
        dependencies.get_workflow_storage(),
        dependencies.get_user_context(),
        file_id,
    )
    return loaded.state if loaded is not None else None


def _load_json_artifact(file_id: str, kind: WorkflowFile) -> object | None:
    stored = dependencies.get_workflow_storage().read_json(
        dependencies.get_user_context(),
        file_id,
        kind,
    )
    return stored.data if stored is not None else None


def _read_downloaded_csv(response_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as archive:
        csv_name = next(name for name in archive.namelist() if name.endswith(".csv"))
        csv_content = archive.read(csv_name).decode("utf-8")
    return list(csv.DictReader(io.StringIO(csv_content)))


def _read_downloaded_tabular(response_bytes: bytes, suffix: str, delimiter: str) -> list[list[str]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as archive:
        data_name = next(name for name in archive.namelist() if name.endswith(suffix))
        content = archive.read(data_name).decode("utf-8")
    return list(csv.reader(io.StringIO(content), delimiter=delimiter))


async def _wait_for_stage_three_job(app_client: AsyncClient, job_id: str, file_id: str) -> dict[str, object]:
    for _ in range(50):
        response = await app_client.get(f"/stage-3/jobs/{job_id}", params={"file_id": file_id})
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return cast(dict[str, object], body)
        await asyncio.sleep(0.02)
    raise AssertionError(f"Stage 3 job did not finish: {job_id}")


def _successful_stage_three_result(file_path: Path, output_path: Path, job_id: str) -> HarmonizeResult:
    """Create the output and manifest evidence required for a successful job."""
    shutil.copy2(file_path, output_path)
    manifest_path = output_path.with_name(f"{job_id}.manifest.parquet")
    create_test_manifest_parquet(manifest_path, [])
    return HarmonizeResult(
        job_id=job_id,
        status=HarmonizeStatus.SUCCEEDED,
        detail="ok",
        manifest_path=manifest_path,
        output_path=output_path,
    )


async def test_stage_three_job_state_rejects_a_timezone_naive_start() -> None:
    # Given a start time without a timezone
    start = datetime(2026, 5, 21)

    # When persisted job state is created
    with pytest.raises(ValueError) as raised:
        HarmonizationJobState.queued(
            polling_job_id="polling-job",
            file_id="deadbeefdeadbeefdeadbeefdeadbeef",
            plan_version="plan-version",
            worker_id="worker-id",
            now=start,
        )

    # Then it rejects the ambiguous time
    assert str(raised.value) == "HarmonizationJobState.started_at must be timezone-aware"


async def test_stage_three_job_state_preserves_a_utc_start() -> None:
    # Given a UTC start time
    start = datetime(2026, 5, 21, tzinfo=UTC)

    # When persisted job state is created
    job = HarmonizationJobState.queued(
        polling_job_id="polling-job",
        file_id="deadbeefdeadbeefdeadbeefdeadbeef",
        plan_version="plan-version",
        worker_id="worker-id",
        now=start,
    )

    # Then it retains the explicit timezone
    assert job.started_at.tzinfo is UTC


async def test_stage1_upload_persists_exact_bytes(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Upload stores the exact CSV bytes for later processing."""

    # Given: a CSV payload and no files in storage yet
    content = create_csv_content([["col_a"], ["alpha"], ["beta"]])
    assert list(temp_storage._data_dir.glob("*.csv")) == []

    # When: the file is uploaded
    file_id = await upload_content(app_client, content, "bytes.csv")

    # Then: stored metadata and file contents match the upload
    meta = temp_storage.load(file_id)
    assert meta is not None, "Expected stored metadata for uploaded file"
    assert meta.size_bytes == len(content), "Stored size does not match upload size"
    assert meta.saved_path.read_bytes() == content, "Stored bytes do not match uploaded bytes"


async def test_stage1_upload_rejects_mismatched_content_type(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Upload rejects non-CSV content types."""

    # Given: CSV bytes with an unsupported content type
    content = create_csv_content([["col_a"], ["alpha"]])
    assert list(temp_storage._data_dir.glob("*.csv")) == []

    # When: the file is uploaded with a mismatched content type
    response = await app_client.post(
        "/stage-1/upload",
        data=upload_integrity_fields(content),
        files={"file": ("bad.json", content, "application/json")},
    )

    # Then: upload is rejected with 415
    assert response.status_code == 415


async def test_stage1_analyze_rejects_invalid_utf8(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze returns 400 for invalid UTF-8 payloads."""

    # Given: bytes that are not valid UTF-8
    content = b"\xff\xfe\xfa\xfb"
    file_id = await upload_content(app_client, content, "invalid.csv")

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: bad request is returned
    assert response.status_code == 400


async def test_stage1_analyze_handles_quoted_commas(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze treats quoted commas as part of the value."""

    # Given: a CSV containing quoted commas
    content = b'col_a\n"alpha, beta"\n'
    file_id = await upload_content(app_client, content, "quoted.csv")

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: analyze succeeds and summarizes the column
    assert response.status_code == 200
    column = response.json()["columns"][0]
    assert column["column_name"] == "col_a"
    assert column["inferred_type"] == "text"


async def test_stage1_analyze_handles_ragged_rows(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze fills missing values for ragged rows."""

    # Given: a CSV with missing values in some rows
    content = b"col_a,col_b\nalpha,beta\ncharlie,\n"
    file_id = await upload_content(app_client, content, "ragged.csv")

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: the full-column confidence reflects the missing cell
    assert response.status_code == 200
    data = response.json()
    col_b = next(col for col in data["columns"] if col["column_name"] == "col_b")
    assert col_b["confidence_score"] == 0.5


async def test_stage1_analyze_accepts_duplicate_headers_with_distinct_column_keys(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze preserves duplicate headers by assigning distinct column keys."""

    # Given: a CSV with duplicate header names
    content = b"col_a,col_a\nalpha,beta\n"
    file_id = await upload_content(app_client, content, "dupe.csv")

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: both duplicate columns are present and independently addressable
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert [column["column_name"] for column in columns] == ["col_a", "col_a"]
    assert [column["column_key"] for column in columns] == ["col_0000", "col_0001"]


async def test_stage1_analyze_accepts_blank_middle_header_by_column_position(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze preserves blank display headers while keeping positional column keys."""

    # Given: a CSV with a blank middle header and no stored manifest yet
    content = b"col_a,,col_c\nalpha,beta,gamma\n"
    file_id = await upload_content(app_client, content, "blank-header.csv")

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: the blank display name is allowed and the source column remains addressable by key
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert [column["column_name"] for column in columns] == ["col_a", "", "col_c"]
    assert [column["column_key"] for column in columns] == ["col_0000", "col_0001", "col_0002"]
    assert [column["source_index"] for column in columns] == [0, 1, 2]


async def test_stage1_analyze_accepts_tsv(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze treats tabs as column delimiters for TSV uploads."""

    # Given: a TSV with commas inside values and no manifest stored yet
    content = b"col_a\tcol_b\nalpha, beta\tgamma\n"
    file_id = await upload_content(app_client, content, "data.tsv", TEST_TSV_CONTENT_TYPE)

    # When: analyze is requested
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: tab-separated columns are parsed
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert [column["column_name"] for column in columns] == ["col_a", "col_b"]


async def test_stage1_analyze_xlsx_defaults_to_first_sheet(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze reads the first worksheet by default for XLSX uploads."""

    # Given: an XLSX workbook with distinct values on each sheet
    content = create_xlsx_content({
        "First": [["first_col"], ["first-value"]],
        "Second": [["second_col"], ["second-value"]],
    })
    file_id = await upload_content(app_client, content, "data.xlsx", TEST_XLSX_CONTENT_TYPE)

    # When: analyze is requested without a sheet override
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: analyzed columns come from the first sheet
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert [column["column_name"] for column in columns] == ["first_col"]


async def test_stage1_analyze_xlsx_uses_selected_sheet(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze uses the sheet selected in Stage 1 for XLSX uploads."""

    # Given: an XLSX workbook with duplicate headers on the second sheet
    content = create_xlsx_content({
        "First": [["ignored"], ["nope"]],
        "Patients": [["col_a", "col_a"], ["alpha", "beta"]],
    })
    file_id = await upload_content(app_client, content, "data.xlsx", TEST_XLSX_CONTENT_TYPE)

    # When: analyze is requested for the second sheet
    response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "sheet_name": "Patients",
        },
    )

    # Then: selected-sheet columns are parsed without collapsing duplicate headers
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert [column["column_name"] for column in columns] == ["col_a", "col_a"]
    assert [column["column_key"] for column in columns] == ["col_0000", "col_0001"]
    state = _load_workflow_state(file_id)
    assert state is not None
    assert state.selected_sheet == "Patients"


async def test_failed_reanalysis_keeps_previous_sheet_selection(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed analysis does not replace the worksheet used by durable state."""
    content = create_xlsx_content({
        "First": [["first_col"], ["first-value"]],
        "Second": [["second_col"], ["second-value"]],
    })
    file_id = await upload_content(app_client, content, "data.xlsx", TEST_XLSX_CONTENT_TYPE)
    first_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "sheet_name": "First",
        },
    )
    assert first_response.status_code == 200

    failing_recommender = MagicMock()
    failing_recommender.recommend = AsyncMock(
        side_effect=RecommendationUnavailableError("provider unavailable")
    )
    monkeypatch.setattr(
        dependencies,
        "get_cde_recommender",
        MagicMock(return_value=failing_recommender),
    )
    failed_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
            "sheet_name": "Second",
        },
    )

    assert failed_response.status_code == 503
    meta = temp_storage.load(file_id)
    assert meta is not None
    assert meta.selected_sheet == "First"
    state = _load_workflow_state(file_id)
    assert state is not None
    assert state.selected_sheet == "First"
    recovered_response = await app_client.get(f"/stage-1/analysis/{file_id}")
    assert recovered_response.status_code == 200
    assert [column["column_name"] for column in recovered_response.json()["columns"]] == ["first_col"]


async def test_stage1_analyze_bom_and_non_bom_match_headers(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """BOM and non-BOM files produce identical headers."""

    # Given: BOM and non-BOM CSVs with the same headers
    bom_content = "\ufeffcol_a,col_b\nalpha,beta\n".encode()
    non_bom_content = b"col_a,col_b\nalpha,beta\n"
    bom_file_id = await upload_content(app_client, bom_content, "bom.csv")
    non_bom_file_id = await upload_content(app_client, non_bom_content, "plain.csv")

    # When: analyze is requested for both
    bom_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": bom_file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
        },
    )
    non_bom_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": non_bom_file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
        },
    )

    # Then: the headers are identical
    assert bom_response.status_code == 200
    assert non_bom_response.status_code == 200
    bom_headers = [col["column_name"] for col in bom_response.json()["columns"]]
    non_bom_headers = [col["column_name"] for col in non_bom_response.json()["columns"]]
    assert bom_headers == non_bom_headers

async def test_stage1_analyze_handles_bom_headers(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Analyze strips BOM headers so column names are correct."""

    # Given: a BOM-prefixed CSV and no manifest stored yet
    content = "\ufeffrecord_id,col_a\nRID-1,Foo\n".encode()
    file_id = await upload_content(app_client, content, "bom.csv")

    # When: the file is analyzed
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: column names do not include BOM characters
    assert response.status_code == 200
    columns = response.json()["columns"]
    assert columns[0]["column_name"] == "record_id"


async def test_stage1_analyze_is_idempotent(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Repeated analysis does not change the stored manifest."""

    # Given: an uploaded CSV with no manifest yet
    content = create_csv_content([["col_a"], ["alpha"], ["beta"]])
    file_id = await upload_content(app_client, content, "idempotent.csv")

    # When: the file is analyzed twice
    response_one = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    response_two = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: manifest and API outputs remain stable
    assert response_one.status_code == 200
    assert response_two.status_code == 200
    assert response_one.json() == response_two.json()


async def test_stage1_analyze_loads_the_selected_reference_model(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    mock_netrias_client: MagicMock,
) -> None:
    """Analyze loads the selected model version through the repository."""

    # Given an uploaded CSV.
    content = create_csv_content([["diagnosis"], ["Lung"], ["Breast"]])
    file_id = await upload_content(app_client, content, "versioned.csv")
    repository = mock_netrias_client.reference_repository
    repository.load_model.reset_mock()

    # When: analysis is requested for GC external version 11.0.4
    response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
        },
    )

    # Then the repository receives the selected external version.
    assert response.status_code == 200
    assert response.json()["external_version_number"] == "11.0.4"
    loaded_version = repository.load_model.call_args.args[0]
    assert loaded_version.data_model_key == "gc"
    assert loaded_version.external_version_number == "11.0.4"


async def test_stage1_analyze_persists_selected_data_model_version(
    app_client: AsyncClient,
) -> None:
    """Analyze saves the selected model/version as durable workflow state."""

    # Given: an uploaded CSV with no workflow selection saved yet
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "selection.csv")
    assert _load_workflow_state(file_id) is None

    # When: analysis is requested for a specific external model version
    response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
        },
    )

    # Then: the selected model/version is available from durable workflow state
    assert response.status_code == 200
    state = _load_workflow_state(file_id)
    assert state is not None
    assert state.file_id == file_id
    assert state.data_model_version.data_model_key == "gc"
    assert state.data_model_version.external_version_number == "11.0.4"


async def test_stage2_mapping_page_loads_selected_model_from_workflow_state(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
) -> None:
    """Stage 2 reloads the selected model/version from durable workflow state."""

    # Given: analysis saved GC external version 11.0.4 in durable workflow state
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "stage2-selection.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
        },
    )
    assert analyze_response.status_code == 200
    repository = mock_netrias_client.reference_repository
    repository.load_model.reset_mock()

    # When: Stage 2 is loaded with only the file id in the URL
    response = await app_client.get(f"/stage-2?file_id={file_id}")

    # Then: the page uses the stored selection to rebuild CDE options
    assert response.status_code == 200
    assert 'dataModelKey: "gc"' in response.text
    assert 'externalVersionNumber: "11.0.4"' in response.text
    assert "stage_2_mappings.js?v=" in response.text
    loaded_version = repository.load_model.call_args.args[0]
    assert loaded_version.data_model_key == "gc"
    assert loaded_version.external_version_number == "11.0.4"


@pytest.mark.parametrize("stage", ["/stage-2", "/stage-3", "/stage-3?job_id=expired-job"])
async def test_retired_model_returns_to_stage_one_with_the_original_upload(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    stage: str,
) -> None:
    # Given an analyzed workbook whose selected version was retired from the catalog.
    content = create_xlsx_content({"First": [["unused"], ["x"]], "Second": [["diagnosis"], ["Lung"]]})
    file_id = await upload_content(app_client, content, "retired-model.xlsx", TEST_XLSX_CONTENT_TYPE)
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
            "sheet_name": "Second",
        },
    )
    assert analysis.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    initial_workflow = load_workflow_state(storage, user, file_id)
    assert initial_workflow is not None
    if "expired-job" in stage:
        expired = HarmonizationJobState.queued(
            polling_job_id="expired-job",
            file_id=file_id,
            plan_version=initial_workflow.version.value,
            worker_id="expired-worker",
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        save_harmonization_job(storage, user, expired, expected_version=None)
    mock_netrias_client.reference_repository.load_model.side_effect = ReferenceModelNotFoundError("retired version")

    # When the user opens Mapping or Harmonize for the saved workflow.
    separator = "&" if "?" in stage else "?"
    response = await app_client.get(f"{stage}{separator}file_id={file_id}", follow_redirects=True)

    # Then Stage 1 can offer model selection with the same file, and the saved analysis is intact.
    assert len(response.history) == 1
    assert response.history[0].status_code == 303
    assert response.url.path == "/stage-1"
    assert response.url.params["file_id"] == file_id
    assert 'reselectModel: true' in response.text
    assert '"file_name": "retired-model.xlsx"' in response.text
    assert '"selected_sheet": "Second"' in response.text
    assert load_workflow_state(storage, user, file_id) == initial_workflow


@pytest.mark.parametrize("retired", [True, False], ids=["retired", "outage"])
async def test_failed_replacement_analysis_preserves_the_confirmed_plan(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    retired: bool,
) -> None:
    # Given a confirmed plan and a replacement version that cannot load.
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "model-removed.csv")
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert response.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    initial_workflow = _load_workflow_state(file_id)
    assert initial_workflow is not None
    mock_netrias_client.reference_repository.load_model.side_effect = (
        ReferenceModelNotFoundError("removed") if retired else ReferenceDataUnavailableError("outage")
    )

    # When the user tries to analyze the same file with the replacement version.
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "12.0.0"},
    )

    # Then analysis distinguishes retirement from an outage and keeps the confirmed plan.
    if retired:
        assert response.status_code == 409
        assert response.json() == {
            "code": "model_version_retired",
            "detail": "This model version is no longer available. Choose a current version and run analysis again.",
        }
    else:
        assert response.status_code == 503
        assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}
    assert _load_workflow_state(file_id) == initial_workflow


@pytest.mark.parametrize("retired", [True, False], ids=["retired", "outage"])
async def test_stage_three_job_preserves_reference_failure_on_resume(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    retired: bool,
) -> None:
    # Given a confirmed plan and a reference load that fails in the worker.
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "job-recovery.csv")
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert response.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    initial_workflow = _load_workflow_state(file_id)
    repository = mock_netrias_client.reference_repository
    repository.load_model.side_effect = (
        ReferenceModelNotFoundError("retired") if retired else ReferenceDataUnavailableError("outage")
    )

    # When a client submits the plan and polls the accepted job through durable storage.
    submitted = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert submitted.status_code == 200
    job = await _wait_for_stage_three_job(app_client, submitted.json()["job_id"], file_id)

    # Then only a retired selection offers recovery, and the confirmed plan is intact.
    assert job["status"] == "failed"
    if retired:
        assert job["detail"] == (
            "This model version is no longer available. Choose a current version and run analysis again."
        )
        assert job["recovery_url"] == f"/stage-1?file_id={file_id}&reselect_model=true"
    else:
        assert job["detail"] == "Reference data is currently unavailable. Please try again later."
        assert job["recovery_url"] is None
    assert _load_workflow_state(file_id) == initial_workflow


@pytest.mark.parametrize("retired", [True, False], ids=["retired", "outage"])
async def test_completed_harmonization_still_opens_when_reference_data_changes(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    retired: bool,
) -> None:
    # Given a finished job with stored results and a catalog that can no longer load its reference version.
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "job-complete.csv")
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analysis.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    submitted = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert submitted.status_code == 200
    job_id = submitted.json()["job_id"]
    completed = await _wait_for_stage_three_job(app_client, job_id, file_id)
    assert completed["status"] == "succeeded"
    repository = mock_netrias_client.reference_repository
    repository.load_model.reset_mock()
    repository.load_model.side_effect = (
        ReferenceModelNotFoundError("retired") if retired else ReferenceDataUnavailableError("outage")
    )

    # When the user reopens the completed job's Stage 3 link.
    page = await app_client.get("/stage-3", params={"file_id": file_id, "job_id": job_id})

    # Then the page can resume from the completed job without loading reference data again.
    assert page.status_code == 200
    assert page.url.path == "/stage-3"
    resumed = await _wait_for_stage_three_job(app_client, job_id, file_id)
    assert resumed["status"] == "succeeded"
    assert resumed["manifest_summary"] == completed["manifest_summary"]
    repository.load_model.assert_not_called()


async def test_recovery_does_not_link_to_a_completed_job_for_another_plan(app_client: AsyncClient) -> None:
    # Given a completed result belonging to an older mapping plan for the same upload.
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "earlier.csv")
    params = {"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"}
    assert (await app_client.post("/stage-1/analyze", json=params)).status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    submitted = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert submitted.status_code == 200
    result = await _wait_for_stage_three_job(app_client, submitted.json()["job_id"], file_id)
    assert result["status"] == "succeeded"
    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    saved = load_harmonization_job(storage, user, file_id)
    assert saved is not None
    earlier = replace(saved.job, plan_version="sha256:" + "0" * 64)
    save_harmonization_job(storage, user, earlier, expected_version=saved.version)

    # When the user opens model recovery for the current plan.
    response = await app_client.get("/stage-1", params={"file_id": file_id, "reselect_model": "true"})

    # Then the upload is still available and the earlier job ID is omitted.
    assert response.status_code == 200
    assert '"file_name": "earlier.csv"' in response.text
    assert '"completedJobId": null' in response.text


async def test_running_harmonization_can_be_reopened_after_model_retirement(
    app_client: AsyncClient, mock_netrias_client: MagicMock
) -> None:
    # Given an analyzed workflow and an in-flight job whose lease is still valid.
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "running.csv")
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analysis.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    workflow = load_workflow_state(dependencies.get_workflow_storage(), dependencies.get_user_context(), file_id)
    assert workflow is not None
    job = HarmonizationJobState.queued(
        polling_job_id="running-job",
        file_id=workflow.state.file_id,
        plan_version=workflow.version.value,
        worker_id="active-worker",
    )
    save_harmonization_job(
        dependencies.get_workflow_storage(), dependencies.get_user_context(), job, expected_version=None
    )
    repository = mock_netrias_client.reference_repository
    repository.load_model.reset_mock()
    repository.load_model.side_effect = ReferenceModelNotFoundError("retired")

    # When the user reopens the active job.
    response = await app_client.get("/stage-3", params={"file_id": file_id, "job_id": job.polling_job_id})

    # Then Stage 3 stays available to poll the job without attempting a new reference load.
    assert response.status_code == 200
    assert "stage_3_harmonize.js?v=" in response.text
    repository.load_model.assert_not_called()


async def test_recovery_does_not_expose_another_users_upload(
    app_client: AsyncClient,
) -> None:
    # Given an upload owned by another user.
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "private-upload.csv")

    # When the user opens a model recovery URL for that upload.
    response = await app_client.get(
        "/stage-1",
        params={"file_id": file_id, "reselect_model": "true"},
        headers={"X-Data-Chord-User-ID": "different-user"},
    )

    # Then Stage 1 denies access without returning the upload metadata.
    assert response.status_code == 403
    assert response.text == "Forbidden"


async def test_stage2_analysis_state_recovers_without_replacing_confirmed_choices(
    app_client: AsyncClient,
) -> None:
    # Given Stage 1 analysis and confirmed Stage 2 choices survived a browser restart.
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "recover.csv")
    analyzed = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analyzed.status_code == 200
    saved = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {"col_0000": "primary_diagnosis"},
            "column_renames": {"col_0000": "Diagnosis"},
        },
    )
    assert saved.status_code == 200

    # When the browser rebuilds its analysis payload from durable state.
    recovered = await app_client.get(f"/stage-1/analysis/{file_id}")

    # Then the payload includes the confirmed choices and durable state is unchanged.
    assert recovered.status_code == 200
    assert recovered.json()["manual_overrides"] == {"col_0000": "primary_diagnosis"}
    assert recovered.json()["column_renames"] == {"col_0000": "Diagnosis"}
    state = _load_workflow_state(file_id)
    assert state is not None
    assert state.mapping_choices is not None
    assert state.mapping_choices.column_overrides.overrides[column_key_from_string("col_0000")] == "primary_diagnosis"


async def test_static_assets_require_browser_revalidation(app_client: AsyncClient) -> None:
    """Deployed browsers must not keep stale stage JavaScript across releases."""

    # Given a versioned Stage 2 asset URL
    asset_url = "/assets/stage-2/stage_2_mappings.js?v=test-release"

    # When the browser requests the asset
    response = await app_client.get(asset_url)

    # Then the response requires cache revalidation
    assert response.status_code == 200
    assert "must-revalidate" in response.headers["Cache-Control"]


async def test_stage3_harmonize_uses_stored_selection(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    temp_storage: UploadStorage,
) -> None:
    """The durable selected model/version is backend truth during harmonization."""

    class StubHarmonizer:
        def __init__(self) -> None:
            self.called = False
            self.sheet_name: str | None = None

        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
            use_cache,
        ):
            self.called = True
            self.sheet_name = sheet_name
            return _successful_stage_three_result(file_path, output_path, "job-selection")

    # Given: analysis saved GC 11.0.4 and the second worksheet in canonical state.
    content = create_xlsx_content({
        "Keep": [["ignored"], ["wrong"]],
        "Patients": [["diagnosis"], ["Lung"], ["Breast"]],
    })
    file_id = await upload_content(
        app_client,
        content,
        "stage3-selection.xlsx",
        TEST_XLSX_CONTENT_TYPE,
    )
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
            "sheet_name": "Patients",
        },
    )
    assert analyze_response.status_code == 200
    upload_meta = temp_storage.load(file_id)
    assert upload_meta is not None
    assert upload_meta.selected_sheet == "Keep"
    await confirm_mapping_choices(app_client, file_id)
    stub = StubHarmonizer()
    repository = mock_netrias_client.reference_repository
    repository.load_model.reset_mock()

    # When: harmonization is triggered from the confirmed workflow
    import unittest.mock

    with unittest.mock.patch("src.app.dependencies.get_harmonize_service", return_value=stub):
        response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
        )

    # Then the stored selection loads the reference model before the harmonizer runs.
    assert response.status_code == 200
    if response.json()["status"] != "succeeded":
        await _wait_for_stage_three_job(app_client, response.json()["job_id"], file_id)
    loaded_version = repository.load_model.call_args.args[0]
    assert loaded_version.data_model_key == "gc"
    assert loaded_version.external_version_number == "11.0.4"
    assert stub.called
    assert stub.sheet_name == "Patients"

    # Stage 4 reads the same worksheet even though upload metadata still names
    # the first worksheet.
    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    assert rows_response.status_code == 200
    assert rows_response.json()["totalOriginalRows"] == 2
    context_response = await app_client.post(
        "/stage-4/row-context",
        json={"file_id": file_id, "row_indices": [0]},
    )
    assert context_response.status_code == 200
    assert context_response.json() == {"headers": ["diagnosis"], "rows": [["Lung"]]}


async def _completed_second_sheet_job(
    app_client: AsyncClient, selected_rows: list[list[object]]
) -> LoadedHarmonizationJob:
    workbook = create_xlsx_content({
        "First": [["diagnosis"], ["Pancreas"]],
        "Selected": [["diagnosis"], *selected_rows],
    })
    file_id = await upload_content(app_client, workbook, "resumed.xlsx", TEST_XLSX_CONTENT_TYPE)
    analyzed = await app_client.post("/stage-1/analyze", json={
        "file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA,
        "external_version_number": "11.0.4", "sheet_name": "Selected",
    })
    assert analyzed.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    started = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert started.status_code == 200
    job_id = started.json()["job_id"]
    assert (await _wait_for_stage_three_job(app_client, job_id, file_id))["status"] == "succeeded"

    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    loaded = load_harmonization_job(storage, user, file_id)
    assert loaded is not None
    summary = loaded.job.manifest_summary
    assert summary is not None and summary.source_row_count == len(selected_rows)
    return loaded


@pytest.fixture
def stage4_storage_overrides(app_client: AsyncClient) -> Iterator[pytest.MonkeyPatch]:
    # Unwind dependency overrides before the application fixture restores its own defaults.
    with pytest.MonkeyPatch.context() as overrides:
        yield overrides


@pytest.mark.parametrize("scenario", ["saved", "saved-zero", "legacy"])
async def test_stage4_row_count_when_resuming(
    app_client: AsyncClient,
    tmp_path: Path,
    test_constraints: UploadConstraints,
    stage4_storage_overrides: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    # Given a completed second-sheet job reopened without its original scratch workspace.
    selected_rows: list[list[object]] = [] if scenario == "saved-zero" else [["Lung"], ["Breast"]]
    loaded = await _completed_second_sheet_job(app_client, selected_rows)
    file_id = loaded.job.file_id
    job_id = loaded.job.polling_job_id
    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    if scenario == "legacy":
        summary = loaded.job.manifest_summary
        assert summary is not None
        legacy = replace(loaded.job, manifest_summary=summary.model_copy(update={"source_row_count": None}))
        save_harmonization_job(storage, user, legacy, expected_version=loaded.version)
    resumed_uploads = UploadStorage(tmp_path / "resumed-uploads", test_constraints)
    stage4_storage_overrides.setattr(dependencies, "get_upload_storage", lambda: resumed_uploads)
    assert resumed_uploads.load(file_id) is None

    # When Stage 4 requests the completed job's review rows.
    with (
        patch("src.stage_4_review_results.use_cases.open_upload_artifact", wraps=open_upload_artifact) as source,
        patch.object(storage, "artifact_version", wraps=storage.artifact_version) as version,
    ):
        response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "job_id": job_id})

    # Then Stage 4 reports the selected sheet count without reading the original, except for legacy jobs.
    assert response.status_code == 200
    assert response.json()["totalOriginalRows"] == len(selected_rows)
    assert source.call_count == (1 if scenario == "legacy" else 0)
    assert (resumed_uploads.load(file_id) is not None) == (scenario == "legacy")
    checked = {call.args[2] for call in version.call_args_list}
    assert checked == {WorkflowFile.HARMONIZED_OUTPUT, WorkflowFile.HARMONIZATION_MANIFEST_BASE}


async def test_stage4_saved_count_still_requires_the_original_upload(
    app_client: AsyncClient,
    tmp_path: Path,
    test_constraints: UploadConstraints,
    stage4_storage_overrides: pytest.MonkeyPatch,
) -> None:
    # Given a completed job with a saved count but no original upload or scratch copy.
    loaded = await _completed_second_sheet_job(app_client, [["Lung"]])
    file_id = loaded.job.file_id
    with dependencies.get_workflow_storage().materialize_artifact(
        dependencies.get_user_context(), file_id, WorkflowFile.ORIGINAL_UPLOAD
    ) as original:
        original.rename(tmp_path / "missing-original.xlsx")
    resumed_uploads = UploadStorage(tmp_path / "scratch", test_constraints)
    stage4_storage_overrides.setattr(dependencies, "get_upload_storage", lambda: resumed_uploads)

    # When Stage 4 opens the completed job.
    response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "job_id": loaded.job.polling_job_id})

    # Then it gives the existing recovery instruction.
    assert response.status_code == 409
    assert response.json() == {"detail": "Upload not found. Return to Stage 1 and upload it again."}


async def test_stage4_reads_the_saved_count_without_downloading_the_s3_upload(
    app_client: AsyncClient,
    tmp_path: Path,
    test_constraints: UploadConstraints,
    stage4_storage_overrides: pytest.MonkeyPatch,
) -> None:
    # Given a completed second-sheet job stored through the production S3 adapter.
    client = FakeS3Client()
    storage = S3WorkflowStorage("offline", client)
    stage4_storage_overrides.setattr(dependencies, "get_workflow_storage", lambda: storage)
    loaded = await _completed_second_sheet_job(app_client, [["Lung"], ["Breast"]])
    file_id = loaded.job.file_id
    original_key, = [key for key in client.objects if key.endswith("/original_upload.xlsx")]
    resumed_uploads = UploadStorage(tmp_path / "scratch", test_constraints)
    stage4_storage_overrides.setattr(dependencies, "get_upload_storage", lambda: resumed_uploads)

    assert resumed_uploads.load(file_id) is None

    # When Stage 4 reopens the result with an empty scratch directory.
    with (
        patch.object(client, "head_object", wraps=client.head_object) as head,
        patch.object(client, "get_object", wraps=client.get_object) as get,
    ):
        response = await app_client.post(
            "/stage-4/rows", json={"file_id": file_id, "job_id": loaded.job.polling_job_id}
        )

    # Then it checks the original exists and returns the saved count without downloading it.
    assert response.status_code == 200
    assert response.json()["totalOriginalRows"] == 2
    assert original_key in {call.kwargs["Key"] for call in head.call_args_list}
    assert original_key not in {call.kwargs["Key"] for call in get.call_args_list}


async def test_stage4_returns_the_missing_upload_message_for_a_missing_s3_original(
    app_client: AsyncClient,
    tmp_path: Path,
    test_constraints: UploadConstraints,
    stage4_storage_overrides: pytest.MonkeyPatch,
) -> None:
    # Given a completed S3-backed job whose original and scratch copy are gone.
    client = FakeS3Client()
    storage = S3WorkflowStorage("offline", client)
    stage4_storage_overrides.setattr(dependencies, "get_workflow_storage", lambda: storage)
    loaded = await _completed_second_sheet_job(app_client, [["Lung"]])
    file_id = loaded.job.file_id
    (original_key,) = [key for key in client.objects if key.endswith("/original_upload.xlsx")]
    client.objects.pop(original_key)
    resumed_uploads = UploadStorage(tmp_path / "scratch", test_constraints)
    stage4_storage_overrides.setattr(dependencies, "get_upload_storage", lambda: resumed_uploads)
    assert resumed_uploads.load(file_id) is None

    # When Stage 4 opens the job with its saved row count.
    response = await app_client.post("/stage-4/rows", json={"file_id": file_id, "job_id": loaded.job.polling_job_id})

    # Then it asks the user to upload the source again.
    assert response.status_code == 409
    assert response.json() == {"detail": "Upload not found. Return to Stage 1 and upload it again."}


async def test_stage3_http_workflow_runs_the_deterministic_term_provider(
    app_client: AsyncClient,
) -> None:
    # Given: a confirmed workflow and the real harmonizer with a no-cost provider.
    class DeterministicProvider:
        def __init__(self) -> None:
            self.requests: list[TermHarmonizationRequest] = []

        def harmonize(
            self,
            requests: tuple[TermHarmonizationRequest, ...],
        ) -> tuple[TermHarmonizationResponse, ...]:
            self.requests.extend(requests)
            return tuple(
                TermHarmonizationResponse(
                    matched_value="Lung Cancer",
                    match_fidelity=MatchFidelity.STRONG,
                )
                for _request in requests
            )

    provider = DeterministicProvider()
    harmonizer = FileHarmonizationService(provider, cache=EmptyHarmonizationCache())
    file_id = await upload_content(
        app_client,
        create_csv_content([["primary_diagnosis"], ["lung malignancy"]]),
        "deterministic-provider.csv",
    )
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
        },
    )
    assert analysis.status_code == 200
    await confirm_mapping_choices(
        app_client,
        file_id,
        manual_overrides={"col_0000": "primary_diagnosis"},
    )

    # When: the HTTP application accepts, runs, polls, and downloads the job.
    import unittest.mock

    with unittest.mock.patch("src.app.dependencies.get_harmonize_service", return_value=harmonizer):
        accepted = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert accepted.status_code == 200
    completed = accepted.json()
    if completed["status"] != "succeeded":
        completed = await _wait_for_stage_three_job(app_client, completed["job_id"], file_id)
    download = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then: one provider term produces the exact durable output and summary.
    assert completed["status"] == "succeeded"
    manifest_summary = cast(dict[str, object], completed["manifest_summary"])
    assert manifest_summary["changed_terms"] == 1
    assert provider.requests == [
        TermHarmonizationRequest(
            cde="primary_diagnosis",
            input_term="lung malignancy",
            permissible_values=("Breast Cancer", "Diabetes", "Hypertension", "Lung Cancer"),
            context="Source column: primary_diagnosis\nTarget CDE: primary_diagnosis",
        )
    ]
    assert download.status_code == 200
    assert _read_downloaded_csv(download.content) == [{"primary_diagnosis": "Lung Cancer"}]


async def test_stage3_rerun_removes_decisions_from_the_previous_result(
    app_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """A fresh Stage 3 result cannot inherit review decisions from an old manifest."""

    class StubHarmonizer:
        def __init__(self) -> None:
            self.run_count = 0

        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
            use_cache,
        ):
            self.run_count += 1
            shutil.copy2(file_path, output_path)
            manifest_path = tmp_path / f"review-reset-{self.run_count}.parquet"
            create_test_manifest_parquet(
                manifest_path,
                [{
                    "job_id": f"review-reset-{self.run_count}",
                    "column_id": 0,
                    "column_name": "diagnosis",
                    "to_harmonize": "Lung",
                    "top_harmonization": "Lung Cancer",
                    "ontology_id": None,
                    "top_harmonizations": ["Lung Cancer"],
                    "match_fidelity": "strong",
                    "error": None,
                    "row_indices": [0],
                }],
            )
            return HarmonizeResult(
                job_id=f"review-reset-{self.run_count}",
                status=HarmonizeStatus.SUCCEEDED,
                detail="ok",
                manifest_path=manifest_path,
                output_path=output_path,
            )

    # Given: a completed Stage 3 result with one saved review decision.
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Lung"]]),
        "review-reset.csv",
    )
    analyzed = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
        },
    )
    assert analyzed.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    stub = StubHarmonizer()
    import unittest.mock

    with unittest.mock.patch(
        "src.app.dependencies.get_harmonize_service",
        return_value=stub,
    ):
        first_run = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
        assert first_run.status_code == 200
        if first_run.json()["status"] != "succeeded":
            await _wait_for_stage_three_job(app_client, first_run.json()["job_id"], file_id)
        saved = await app_client.post(
            "/stage-4/overrides",
            headers=await review_precondition(app_client, file_id),
            json={
                "file_id": file_id,
                "overrides": {
                    "1": {
                        "col_0000": {
                            "human_value": "Reviewed Lung",
                            "original_value": "Lung",
                        },
                    },
                },
                "review_state": review_state_payload(),
            },
        )
        assert saved.status_code == 200

        # When: Stage 3 runs again for the same confirmed mapping.
        second_run = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
        assert second_run.status_code == 200
        if second_run.json()["status"] != "succeeded":
            await _wait_for_stage_three_job(app_client, second_run.json()["job_id"], file_id)

    # Then: review starts empty against the new immutable manifest.
    rows = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    assert rows.status_code == 200
    assert rows.json()["reviewState"] is None
    assert stub.run_count == 2


async def test_stage3_harmonize_returns_queued_while_long_job_finishes(
    app_client: AsyncClient,
) -> None:
    """Stage 3 does not keep the browser request open for slow harmonization jobs."""

    class SlowStubHarmonizer:
        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
            use_cache,
        ):
            time.sleep(0.5)
            return _successful_stage_three_result(file_path, output_path, "job-slow")

    # Given: an analyzed upload has no completed Stage 3 job yet
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "slow-stage3.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    await confirm_mapping_choices(app_client, file_id)

    # When: harmonization is triggered and the harmonizer is still running
    import unittest.mock

    with unittest.mock.patch("src.app.dependencies.get_harmonize_service", return_value=SlowStubHarmonizer()):
        response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
        )

        # Then: the browser gets a queued job promptly and can poll it to completion
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert isinstance(body["elapsed_seconds"], int)
        queued_next_stage_url = body["next_stage_url"]
        assert isinstance(queued_next_stage_url, str)
        assert "status=queued" in queued_next_stage_url
        assert body["manifest_summary"] is None

        finished = await _wait_for_stage_three_job(app_client, body["job_id"], file_id)

    assert finished["status"] == "succeeded"
    assert finished["job_id"] == "job-slow"
    assert isinstance(finished["elapsed_seconds"], int)
    finished_next_stage_url = finished["next_stage_url"]
    assert isinstance(finished_next_stage_url, str)
    assert "status=succeeded" in finished_next_stage_url


async def test_stage3_job_status_reads_durable_state_after_worker_task_ends(
    app_client: AsyncClient,
) -> None:
    """Stage 3 polling reads the completed job from durable workflow state."""

    class SlowStubHarmonizer:
        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
            use_cache,
        ):
            time.sleep(0.5)
            return _successful_stage_three_result(file_path, output_path, "job-durable")

    # Given: a slow Stage 3 job has been accepted and later completed
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "durable-stage3.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    assert _load_json_artifact(file_id, WorkflowFile.STAGE_THREE_JOB) is None

    import unittest.mock

    with unittest.mock.patch("src.app.dependencies.get_harmonize_service", return_value=SlowStubHarmonizer()):
        response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
        )
        assert response.status_code == 200
        accepted_job_id = response.json()["job_id"]
        assert _load_json_artifact(file_id, WorkflowFile.STAGE_THREE_JOB) is not None

        finished = await _wait_for_stage_three_job(app_client, accepted_job_id, file_id)

    # When: a later request polls using only durable workflow state
    recovered_response = await app_client.get(
        f"/stage-3/jobs/{accepted_job_id}",
        params={"file_id": file_id},
    )

    # Then: the poll endpoint falls back to workflow storage
    assert finished["status"] == "succeeded"
    assert recovered_response.status_code == 200
    recovered = recovered_response.json()
    assert recovered["status"] == "succeeded"
    assert recovered["job_id"] == "job-durable"
    assert isinstance(recovered["elapsed_seconds"], int)


async def test_stage2_saves_confirmed_mapping_choices_to_workflow_state(
    app_client: AsyncClient,
) -> None:
    """Stage 2 persists user mapping and rename choices before Stage 3."""

    # Given: analysis has created workflow state for an uploaded file
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "mapping-choices.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    state = _load_workflow_state(file_id)
    assert state is not None
    assert state.mapping_choices is None

    # When: Stage 2 saves confirmed choices
    response = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {"col_0000": "primary_diagnosis", "col_0001": None},
            "column_renames": {"col_0000": "Primary Diagnosis"},
        },
    )

    # Then: the choices are durable workflow state
    assert response.status_code == 200
    updated = _load_workflow_state(file_id)
    assert updated is not None
    assert updated.mapping_choices is not None
    assert updated.mapping_choices.column_overrides.to_strings() == {
        "col_0000": "primary_diagnosis",
        "col_0001": None,
    }
    assert updated.mapping_choices.column_renames.to_strings() == {"col_0000": "Primary Diagnosis"}


async def test_stage2_save_mapping_choices_requires_workflow_state(
    app_client: AsyncClient,
) -> None:
    """Stage 2 reports a clear error when choices are saved before analysis."""

    # Given: a file was uploaded, but Stage 1 analysis has not created workflow state
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "no-state.csv")
    assert _load_workflow_state(file_id) is None

    # When: Stage 2 tries to persist confirmed choices
    response = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {"col_0000": "primary_diagnosis"},
            "column_renames": {"col_0000": "Primary Diagnosis"},
        },
    )

    # Then: the endpoint preserves the 404 while hiding route internals
    assert response.status_code == 404
    assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL


async def test_stage3_harmonize_prefers_stored_mapping_choices_over_stale_request(
    app_client: AsyncClient,
) -> None:
    """The confirmed Stage 2 choices are backend truth during harmonization."""

    class StubHarmonizer:
        def __init__(self) -> None:
            self.received_manifest: ManifestPayload | None = None

        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
            use_cache,
        ):
            self.received_manifest = prepared_manifest.to_payload()
            return _successful_stage_three_result(file_path, output_path, "job-choices")

    # Given: Stage 2 saved confirmed mapping choices
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "stage3-choices.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    choices_response = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {"col_0000": "primary_diagnosis", "col_0001": None},
            "column_renames": {"col_0000": "Primary Diagnosis"},
        },
    )
    assert choices_response.status_code == 200
    stub = StubHarmonizer()

    # When: Stage 3 runs from the confirmed workflow
    import unittest.mock

    with unittest.mock.patch("src.app.dependencies.get_harmonize_service", return_value=stub):
        response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
        )

    # Then: Stage 3 uses the confirmed choices from workflow state
    assert response.status_code == 200
    assert stub.received_manifest is not None
    provider_mappings = stub.received_manifest["column_mappings"]
    assert provider_mappings["col_0000"]["cde_key"] == "primary_diagnosis"
    assert provider_mappings["col_0000"].get("column_name") == "Primary Diagnosis"
    assert "col_0001" not in provider_mappings


async def test_stage3_applies_confirmed_column_renames_to_download(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    tmp_path: Path,
) -> None:
    """Confirmed Stage 2 rename choices become final download headers."""

    class StubHarmonizer:
        def __init__(self) -> None:
            self.received_renames: dict[str, str] | None = None

        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
            use_cache,
        ):
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerows([["diagnosis"], ["Lung Cancer"]])
            assert output_path.read_text(encoding="utf-8").splitlines()[0] == "diagnosis"
            manifest_path = tmp_path / "rename-manifest.parquet"
            create_test_manifest_parquet(
                manifest_path,
                [
                    {
                        "job_id": "job-renamed-download",
                        "column_id": 0,
                        "column_name": "diagnosis",
                        "to_harmonize": "Lung",
                        "top_harmonization": "Lung Cancer",
                        "ontology_id": None,
                        "top_harmonizations": ["Lung Cancer"],
                        "match_fidelity": "strong",
                        "error": None,
                        "row_indices": [0],
                        "manual_overrides": [],
                    }
                ],
            )
            return HarmonizeResult(
                job_id="job-renamed-download",
                status=HarmonizeStatus.SUCCEEDED,
                detail="ok",
                manifest_path=manifest_path,
            )

    # Given: Stage 2 saved a confirmed output rename for the analyzed column
    file_id = await upload_content(app_client, create_csv_content([["diagnosis"], ["Lung"]]), "renamed-flow.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    choices_response = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {},
            "column_renames": {"col_0000": "Primary Diagnosis"},
        },
    )
    assert choices_response.status_code == 200

    # When: Stage 3 runs and Stage 5 downloads the final dataset
    import unittest.mock

    with unittest.mock.patch("src.app.dependencies.get_harmonize_service", return_value=StubHarmonizer()):
        harmonize_response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
        )
    assert harmonize_response.status_code == 200

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})
    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then: review, summary, and download use the confirmed output header, not the source header
    assert rows_response.status_code == 200
    assert rows_response.json()["columns"][0]["columnLabel"] == "Primary Diagnosis"
    assert summary_response.status_code == 200
    assert summary_response.json()["column_summaries"][0]["column"] == "Primary Diagnosis"
    assert download_response.status_code == 200
    output_rows = _read_downloaded_csv(download_response.content)
    assert list(output_rows[0]) == ["Primary Diagnosis"]
    assert output_rows[0]["Primary Diagnosis"] == "Lung Cancer"


async def test_stage3_column_renames_propagate_when_output_name_matches_existing_header(
    app_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """Rename choices are keyed by column identity, even when output names repeat."""

    class StubHarmonizer:
        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
            use_cache,
        ):
            self.received_renames = {
                str(column_key): record.column_name
                for column_key, record in prepared_manifest.records.items()
                if record.column_name is not None
            }
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerows([["disease_type", "disease_type"], ["stale", ""]])
            actual_output_path = output_path.with_name(f"{output_path.stem}.v2{output_path.suffix}")
            with actual_output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerows([["diagnosis", "disease_type"], ["Lung Cancer", ""]])
            manifest_path = tmp_path / "repeat-output-name-manifest.parquet"
            create_test_manifest_parquet(
                manifest_path,
                [
                    {
                        "job_id": "job-repeat-output-name",
                        "column_id": 0,
                        "column_name": "diagnosis",
                        "to_harmonize": "Lung",
                        "top_harmonization": "Lung Cancer",
                        "ontology_id": None,
                        "top_harmonizations": ["Lung Cancer"],
                        "match_fidelity": "strong",
                        "error": None,
                        "row_indices": [0],
                        "manual_overrides": [],
                    }
                ],
            )
            return HarmonizeResult(
                job_id="job-repeat-output-name",
                status=HarmonizeStatus.SUCCEEDED,
                detail="ok",
                manifest_path=manifest_path,
                output_path=actual_output_path,
            )

    # Given: the uploaded file already contains the target standard column name
    stub = StubHarmonizer()
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis", "disease_type"], ["Lung", ""]]),
        "repeat-output-name-flow.csv",
    )
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )
    assert analyze_response.status_code == 200
    choices_response = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {},
            "column_renames": {"col_0000": "disease_type"},
        },
    )
    assert choices_response.status_code == 200

    # When: Stage 3 runs and Stage 5 downloads the final dataset
    import unittest.mock

    with unittest.mock.patch("src.app.dependencies.get_harmonize_service", return_value=stub):
        harmonize_response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": file_id},
        )
    assert harmonize_response.status_code == 200

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})
    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    cde_mapping = _load_json_artifact(file_id, WorkflowFile.CDE_MAPPING)

    # Then: the selected output name propagates for col_0000 without merging it into col_0001
    assert rows_response.status_code == 200
    assert stub.received_renames["col_0000"] == "disease_type"
    assert [column["columnLabel"] for column in rows_response.json()["columns"]] == ["disease_type"]
    assert summary_response.status_code == 200
    assert [summary["column"] for summary in summary_response.json()["column_summaries"]] == ["disease_type"]
    assert download_response.status_code == 200
    output_rows = _read_downloaded_tabular(download_response.content, ".csv", ",")
    output_headers = output_rows[0]
    assert output_headers == ["disease_type", "disease_type"]
    assert len(output_headers) == 2
    assert len(set(output_headers)) == 1
    assert output_rows[1] == ["Lung Cancer", ""]
    assert isinstance(cde_mapping, dict)
    mappings = {mapping["column_key"]: mapping for mapping in cde_mapping["mappings"]}
    assert mappings["col_0000"]["output_column_name"] == "disease_type"
    assert mappings["col_0001"]["output_column_name"] == "disease_type"


async def test_stage3_persists_cde_mapping_download_artifact(
    app_client: AsyncClient,
) -> None:
    """Harmonize saves the column-to-CDE mapping plan for the download bundle."""

    # Given: an uploaded CSV and a manifest with two mapped columns
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis", "drug"], ["Lung", "Agent A"]]),
        "mapping-plan.csv",
    )
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
        },
    )
    assert analyze_response.status_code == 200
    choices_response = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {"col_0001": "primary_diagnosis"},
            "column_renames": {"col_0001": "Treatment Diagnosis"},
        },
    )
    assert choices_response.status_code == 200

    # When: the user overrides and renames the second column
    response = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id},
    )

    # Then a mapping artifact records overlap suggestions, user overrides, and output names by column key.
    assert response.status_code == 200
    document = _load_json_artifact(file_id, WorkflowFile.CDE_MAPPING)
    assert isinstance(document, dict)
    mappings = {entry["column_key"]: entry for entry in document["mappings"]}
    assert mappings["col_0000"]["mapping_source"] == "value_overlap"
    assert mappings["col_0000"]["cde_key"] == "tissue_or_organ_of_origin"
    assert mappings["col_0001"]["mapping_source"] == "user_override"
    assert mappings["col_0001"]["source_column_name"] == "drug"
    assert mappings["col_0001"]["output_column_name"] == "Treatment Diagnosis"
    assert mappings["col_0001"]["cde_description"] == "Primary Diagnosis"


async def test_stage2_mapping_page_renders_manual_options(
    app_client: AsyncClient,
) -> None:
    """Stage 2 mapping page exposes CDE labels for manual mapping."""

    # Given the repository contains the requested model.
    file_id = "deadbeefdeadbeefdeadbeefdeadbeef"

    # When: the mapping page is requested with data model version query params
    response = await app_client.get(
        f"/stage-2?file_id={file_id}&data_model_key=test-data-model&external_version_number=11.0.4"
    )

    # Then: the page renders and includes CDE labels
    assert response.status_code == 200
    assert "primary_diagnosis" in response.text


async def test_stage2_mapping_page_includes_default_data_model_key(
    app_client: AsyncClient,
) -> None:
    """Stage 2 mapping page renders the data model key from query param."""

    # Given: the mapping page has not been loaded yet
    # When: the mapping page is requested with data model key query param
    response = await app_client.get("/stage-2?data_model_key=test-data-model")

    # Then: the data model key is embedded for client-side use
    assert response.status_code == 200
    assert 'dataModelKey: "test-data-model"' in response.text
