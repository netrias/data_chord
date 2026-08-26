"""Provide shared fixtures for feature-level testing."""

from __future__ import annotations

import csv
import shutil
import tempfile
from collections.abc import AsyncGenerator, Generator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from starlette.datastructures import Headers

from src.storage import UploadConstraints, UploadStorage

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Test constants
TEST_CSV_CONTENT_TYPE = "text/csv"
TEST_TSV_CONTENT_TYPE = "text/tab-separated-values"
TEST_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEST_TARGET_SCHEMA = "CCDI"
TEST_TARGET_EXTERNAL_VERSION_NUMBER = "11.0.4"
SAMPLE_CSV_ROW_COUNT = 10
SAMPLE_CSV_COLUMN_COUNT = 6
MAX_EXAMPLES_LIMIT = 20
MANUAL_COLUMN_CONFIDENCE = 0.2


def review_state_payload() -> dict[str, object]:
    """why: reuse a consistent review state payload across tests."""
    return {
        "review_mode": "column",
        "sort_mode": "original",
        "column_mode": {
            "current_unit": 1,
            "batch_size": 5,
        },
        "row_mode": {
            "current_unit": 1,
            "batch_size": 5,
        },
    }


@dataclass
class MockCDEMappingResult:
    """why: simulate the structure returned by NetriasClient.discover_cde_mapping."""

    suggestions: list[Any]
    raw: dict[str, Any]


@dataclass
class MockMappingSuggestion:
    """why: simulate individual column mapping suggestions."""

    source_column: str
    options: list[Any]


@dataclass
class MockMappingOption:
    """why: simulate a single CDE target option."""

    target: str
    confidence: float


@dataclass
class MockHarmonizeResult:
    """why: simulate the structure returned by NetriasClient.harmonize."""

    status: str
    description: str
    job_id: str | None = None
    mapping_id: str | None = None
    manifest_path: Path | None = None
    file_path: Path | None = None


@pytest.fixture
def test_constraints() -> UploadConstraints:
    """why: provide smaller limits for faster test execution."""
    return UploadConstraints(max_bytes=25 * 1024 * 1024)


@pytest.fixture
def temp_storage(tmp_path: Path, test_constraints: UploadConstraints) -> UploadStorage:
    """why: isolate test file storage from production uploads."""
    return UploadStorage(tmp_path / "uploads", test_constraints)


@pytest.fixture
def mock_netrias_client() -> Generator[MagicMock]:
    """Temporary fixture name retained while feature tests move to local services."""
    import src.app.dependencies as deps
    from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
    from src.domain.cde_catalog import CdeCatalog
    from src.domain.cde_pv_catalog import CdePvCatalog
    from src.domain.data_model_version_reference import DataModelVersionReference
    from src.domain.harmonization import HarmonizeStatus
    from src.domain.reference_data import ReferenceModel
    from src.integrations.harmonize import HarmonizeResult
    from src.integrations.value_overlap_cde_recommendation import ValueOverlapCdeRecommender

    model_key = "test-data-model"
    version = "11.0.4"
    values = {
        "record_id": frozenset(f"R{index:03d}" for index in range(1, 20)),
        "therapeutic_agents": frozenset({"Aspirin", "Ibuprofen", "Metformin", "Lisinopril"}),
        "primary_diagnosis": frozenset({"Lung Cancer", "Breast Cancer", "Diabetes", "Hypertension"}),
        "morphology": frozenset({"Adenocarcinoma", "Ductal Carcinoma", "N/A"}),
        "tissue_or_organ_of_origin": frozenset({"Lung", "Breast", "Pancreas", "Heart"}),
        "sample_anatomic_site": frozenset({"Right Lung", "Left Breast", "Pancreas", "Heart"}),
    }
    reference_model = ReferenceModel(
        version=DataModelVersionReference(model_key, version),
        label="Test Data Model",
        catalog=CdeCatalog.from_cdes(
            [CDEInfo(None, cde_key, cde_key.replace("_", " ").title(), CdeType.PV) for cde_key in values]
        ),
        pvs=CdePvCatalog.from_mapping(values),
    )
    repository = MagicMock()
    repository.list_models.return_value = (
        DataModelSummary(model_key, "Test Data Model", [DataModelVersionInfo(version)]),
    )
    repository.load_model.return_value = reference_model

    def _successful_harmonization(**kwargs: object) -> HarmonizeResult:
        source_path = Path(str(kwargs["file_path"]))
        output_path = Path(str(kwargs["output_path"]))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, output_path)
        manifest_path = output_path.with_name(f"{output_path.stem}.manifest.parquet")
        create_test_manifest_parquet(manifest_path, [])
        return HarmonizeResult(
            job_id="mock-job-id-12345",
            status=HarmonizeStatus.SUCCEEDED,
            detail="Harmonization completed.",
            manifest_path=manifest_path,
            output_path=output_path,
        )

    harmonizer = MagicMock()
    harmonizer.run.side_effect = _successful_harmonization
    harmonizer.reference_repository = repository
    original_repository = deps._reference_data_repository
    original_harmonizer = deps._harmonize_service
    original_cde_recommender = deps._cde_recommender
    deps._reference_data_repository = repository
    deps._harmonize_service = harmonizer
    deps._cde_recommender = ValueOverlapCdeRecommender()
    yield harmonizer
    deps._reference_data_repository = original_repository
    deps._harmonize_service = original_harmonizer
    deps._cde_recommender = original_cde_recommender


@pytest.fixture
def sample_csv_path() -> Path:
    """why: provide path to the standard test CSV fixture."""
    return FIXTURES_DIR / "sample.csv"


@pytest.fixture
def types_csv_path() -> Path:
    """why: provide path to the mixed-types test fixture."""
    return FIXTURES_DIR / "types.csv"


@pytest.fixture
def with_nulls_csv_path() -> Path:
    """why: provide path to the null-variation test fixture."""
    return FIXTURES_DIR / "with_nulls.csv"


@pytest.fixture
async def app_client(
    temp_storage: UploadStorage,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient]:
    """why: provide an async HTTP client for testing the full API."""
    monkeypatch.setenv("DATA_CHORD_REFERENCE_TABLE", "test-reference-table")
    monkeypatch.setenv("DATA_CHORD_HARMONIZATION_CACHE_TABLE", "test-cache-table")
    monkeypatch.setenv("DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE", "test-cde-cache-table")
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "trusted_proxy")
    from src.auth.user_context import bind_user_context, reset_user_context

    user_token = bind_user_context(Headers({"x-data-chord-user-id": "test-user"}))
    import src.app.dependencies as deps_module
    from src.storage import LocalWorkflowStorage

    original_storage = deps_module._storage
    deps_module._storage = temp_storage

    original_get_storage = deps_module.get_upload_storage
    deps_module.get_upload_storage = lambda: temp_storage

    original_workflow_storage = deps_module._workflow_storage
    original_get_workflow_storage = deps_module.get_workflow_storage
    test_workflow_storage = LocalWorkflowStorage(temp_storage._base_dir / "workflow_storage")
    deps_module._workflow_storage = test_workflow_storage
    deps_module.get_workflow_storage = lambda: test_workflow_storage

    try:
        from backend.app.main import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Data-Chord-User-ID": "test-user"},
        ) as client:
            yield client
    finally:
        reset_user_context(user_token)
        deps_module._storage = original_storage
        deps_module.get_upload_storage = original_get_storage
        deps_module._workflow_storage = original_workflow_storage
        deps_module.get_workflow_storage = original_get_workflow_storage


@pytest.fixture
def upload_csv_content() -> bytes:
    """why: provide raw CSV bytes for upload tests."""
    return (FIXTURES_DIR / "sample.csv").read_bytes()


def create_csv_content(rows: list[list[str]]) -> bytes:
    """why: dynamically generate CSV content for specific test scenarios."""
    lines: list[str] = [",".join(row) for row in rows]
    return "\n".join(lines).encode("utf-8")


def create_xlsx_content(sheets: dict[str, list[list[object]]]) -> bytes:
    """why: dynamically generate XLSX content for workbook selection tests."""
    from io import BytesIO
    from typing import cast

    workbook = Workbook()
    default_sheet = cast(Worksheet, workbook.active)
    for index, (sheet_name, rows) in enumerate(sheets.items()):
        sheet = default_sheet if index == 0 else cast(Worksheet, workbook.create_sheet(sheet_name))
        sheet.title = sheet_name
        for row in rows:
            sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


async def upload_file(client: AsyncClient, csv_path: Path) -> str:
    """why: upload a file and return its file_id for use in subsequent test steps."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (csv_path.name, csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    assert response.status_code == 201, f"Upload failed: {response.status_code} {response.text}"
    return response.json()["file_id"]


async def upload_content(
    client: AsyncClient,
    content: bytes,
    filename: str = "test.csv",
    content_type: str = TEST_CSV_CONTENT_TYPE,
) -> str:
    """why: upload raw content and return its file_id for dynamic test scenarios."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == 201, f"Upload failed: {response.status_code} {response.text}"
    return response.json()["file_id"]


async def upload_and_analyze(client: AsyncClient, csv_path: Path) -> str:
    """Upload, analyze, and confirm the current mapping plan."""
    file_id = await upload_file(client, csv_path)
    analysis = await client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": TEST_TARGET_EXTERNAL_VERSION_NUMBER,
        },
    )
    assert analysis.status_code == 200
    await confirm_mapping_choices(client, file_id)
    return file_id


async def confirm_mapping_choices(
    client: AsyncClient,
    file_id: str,
    *,
    manual_overrides: Mapping[str, str | None] | None = None,
    column_renames: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> None:
    """Confirm the current Stage 2 plan before a Stage 3 request."""
    response = await client.post(
        "/stage-2/choices",
        headers=headers,
        json={
            "file_id": file_id,
            "manual_overrides": manual_overrides or {},
            "column_renames": column_renames or {},
        },
    )
    assert response.status_code == 200, response.text


def create_harmonized_csv(
    storage: UploadStorage,
    file_id: str,
    original_path: Path,
    changes: dict[int, dict[str, str]],
) -> Path:
    """why: create a managed harmonized CSV with specified changes."""
    with original_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []

    for row_idx, column_changes in changes.items():
        if row_idx < len(rows):
            rows[row_idx].update(column_changes)

    harmonized_path = storage.harmonized_path_for(file_id, original_path)
    with harmonized_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    return harmonized_path


def create_test_manifest_parquet(
    output_path: Path,
    rows: list[dict[str, Any]],
) -> Path:
    """why: create a test manifest.parquet file using the canonical schema."""
    from src.persistence.manifest_schema import get_manifest_schema

    schema = get_manifest_schema()

    arrays = {
        "job_id": [row.get("job_id", "test-job") for row in rows],
        "column_id": [row.get("column_id", 0) for row in rows],
        "column_name": [row.get("column_name", "") for row in rows],
        "to_harmonize": [row.get("to_harmonize", "") for row in rows],
        "top_harmonization": [row.get("top_harmonization", "") for row in rows],
        "ontology_id": [row.get("ontology_id") for row in rows],
        "top_harmonizations": [row.get("top_harmonizations", []) for row in rows],
        "match_fidelity": [row.get("match_fidelity", "strong") for row in rows],
        "error": [row.get("error") for row in rows],
        "row_indices": [row.get("row_indices", []) for row in rows],
        "manual_overrides": [row.get("manual_overrides", []) for row in rows],
    }

    table = pa.table(arrays, schema=schema)
    pq.write_table(table, output_path)
    return output_path


def store_test_harmonization_manifest(
    storage: UploadStorage,
    file_id: str,
    rows: list[dict[str, Any]],
) -> Path:
    """Create complete Stage 3 evidence through the production storage boundaries."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / f"{file_id}_test_manifest.parquet"
        create_test_manifest_parquet(temp_path, rows)
        stored_path = storage.save_harmonization_manifest(file_id, temp_path)
    _seed_test_workflow_state(file_id, rows)
    harmonized_path = storage.load_harmonized_path(file_id)
    if harmonized_path is None:
        _store_test_harmonization_manifest(file_id, stored_path)
    elif _store_test_stage_three_artifacts(file_id, harmonized_path, stored_path):
        _store_test_completed_stage_three_job(file_id)
    return stored_path


def store_test_completed_harmonization(
    storage: UploadStorage,
    file_id: str,
    harmonized_path: Path,
    *,
    manifest_path: Path | None = None,
    manifest_rows: list[dict[str, Any]] | None = None,
) -> None:
    """Publish the durable output and terminal job state owned by successful Stage 3."""
    if not _seed_test_workflow_state(file_id, manifest_rows or []):
        return
    if not _store_test_stage_three_artifacts(file_id, harmonized_path, manifest_path):
        return
    _store_test_completed_stage_three_job(file_id)


def _seed_test_workflow_state(file_id: str, rows: list[dict[str, Any]]) -> bool:
    """Give hand-built Stage 3 fixtures the canonical plan required by later stages."""
    from netrias_client import read_tabular

    import src.app.dependencies as dependencies
    from src.domain.columns import ColumnKey, column_key_for_index
    from src.domain.data_model_version_reference import DataModelVersionReference
    from src.domain.manifest import ColumnMappingManifest, ColumnMappingRecord
    from src.domain.workflow_state import WorkflowState
    from src.persistence.workflow_state_store import load_workflow_state, save_initial_workflow_state
    from src.storage import WorkflowStorageError

    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        loaded = load_workflow_state(workflow_storage, user, file_id)
        if loaded is not None:
            return True
        records: dict[ColumnKey, ColumnMappingRecord] = {}
        for row in rows:
            column_id = row.get("column_id")
            column_name = row.get("column_name")
            if not isinstance(column_id, int) or not isinstance(column_name, str):
                continue
            column_key = column_key_for_index(column_id)
            cde_key = column_name if column_name in {"primary_diagnosis", "therapeutic_agents"} else f"cde_{column_id}"
            records[column_key] = ColumnMappingRecord(
                column_key=column_key,
                cde_key=cde_key,
                cde_id=column_id + 1,
                column_name=column_name,
            )
        meta = dependencies.get_upload_storage().load(file_id)
        if meta is not None:
            dataset = read_tabular(meta.saved_path, sheet_name=meta.selected_sheet)
            for column in dataset.columns:
                column_key = column_key_for_index(column.index)
                if column_key in records:
                    continue
                cde_key = (
                    column.header
                    if column.header in {"primary_diagnosis", "therapeutic_agents"}
                    else f"cde_{column.index}"
                )
                records[column_key] = ColumnMappingRecord(
                    column_key=column_key,
                    cde_key=cde_key,
                    cde_id=column.index + 1,
                    column_name=column.header,
                )
        save_initial_workflow_state(
            workflow_storage,
            user,
            WorkflowState.from_data_model_version(
                file_id,
                DataModelVersionReference(TEST_TARGET_SCHEMA, TEST_TARGET_EXTERNAL_VERSION_NUMBER),
                ColumnMappingManifest(records),
                selected_sheet=meta.selected_sheet if meta is not None else None,
            ),
        )
        return True
    except WorkflowStorageError:
        # Cross-owner tests already seed state through Stage 1/3 under the
        # request identity; the default test context must not impersonate it.
        return False


def _store_test_harmonization_manifest(file_id: str, manifest_path: Path) -> None:
    """Persist a manifest-only fixture without claiming Stage 3 completed."""
    import src.app.dependencies as dependencies
    from src.storage import WorkflowFile, WorkflowStorageError

    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        workflow_storage.write_artifact(
            user,
            file_id,
            WorkflowFile.HARMONIZATION_MANIFEST_BASE,
            manifest_path,
        )
    except WorkflowStorageError:
        return


def _store_test_stage_three_artifacts(
    file_id: str,
    harmonized_path: Path,
    manifest_path: Path | None,
) -> bool:
    """Persist completed Stage 3 artifacts through the production boundary."""
    import src.app.dependencies as dependencies
    from src.persistence.workflow_artifacts import save_harmonized_artifacts
    from src.storage import WorkflowStorageError

    try:
        save_harmonized_artifacts(
            dependencies.get_workflow_storage(),
            dependencies.get_user_context(),
            file_id,
            harmonized_path,
            manifest_path,
        )
        return True
    except WorkflowStorageError:
        return False


def _store_test_completed_stage_three_job(file_id: str) -> None:
    """Tie fixture completion to the exact canonical workflow revision."""
    from dataclasses import replace
    from datetime import UTC, datetime

    import src.app.dependencies as dependencies
    from src.domain.harmonization import HarmonizeStatus
    from src.persistence.harmonization_job_store import (
        HarmonizationJobState,
        load_harmonization_job,
        save_harmonization_job,
    )
    from src.persistence.workflow_state_store import load_workflow_state
    from src.storage import WorkflowStorageError

    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        loaded_state = load_workflow_state(workflow_storage, user, file_id)
        if loaded_state is None:
            return
        existing_job = load_harmonization_job(workflow_storage, user, file_id)
        now = datetime.now(UTC)
        job_id = f"test-job-{file_id}"
        completed_job = replace(
            HarmonizationJobState.queued(
                polling_job_id=job_id,
                file_id=file_id,
                plan_version=loaded_state.version.value,
                worker_id="test-fixture-worker",
                now=now,
            ),
            status=HarmonizeStatus.SUCCEEDED,
            detail="Harmonization completed.",
            job_id_available=True,
            lease_expires_at=now,
        )
        save_harmonization_job(
            workflow_storage,
            user,
            completed_job,
            expected_version=existing_job.version if existing_job is not None else None,
        )
    except WorkflowStorageError:
        return


def save_test_pvs_by_column(file_id: str, pvs_by_column_key: dict[str, frozenset[str]]) -> None:
    """Persist PV evidence against the exact canonical workflow revision."""
    import src.app.dependencies as dependencies
    from src.domain.cde_pv_catalog import CdePvCatalog
    from src.domain.columns import column_key_from_string
    from src.persistence.pv_manifest_store import effective_column_cde_map, save_pv_snapshot
    from src.persistence.workflow_state_store import load_workflow_state

    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    loaded = load_workflow_state(workflow_storage, user, file_id)
    assert loaded is not None
    column_cde_map = effective_column_cde_map(loaded)
    catalog: dict[str, frozenset[str]] = {}
    for raw_column_key, pvs in pvs_by_column_key.items():
        column_key = column_key_from_string(raw_column_key)
        cde_key = column_cde_map.mappings.get(column_key)
        assert cde_key is not None, f"Fixture workflow has no CDE mapping for {raw_column_key}"
        catalog[cde_key] = pvs
    save_pv_snapshot(workflow_storage, user, loaded, CdePvCatalog.from_mapping(catalog))


def _get_columns_with_changes(changes: dict[int, dict[str, str]], headers: list[str]) -> set[str]:
    """Extract column names that have changes, or default to first two columns."""
    columns = {col for col_changes in changes.values() for col in col_changes}
    if not columns:
        columns = set(headers[:2]) if len(headers) >= 2 else set(headers)
    return columns


def _build_manifest_row(
    file_id: str,
    col_name: str,
    row_idx: int,
    original_value: str,
    harmonized_value: str,
    headers: list[str],
) -> dict[str, Any]:
    """Build a single manifest row dict."""
    return {
        "job_id": f"test-job-{file_id}",
        "column_id": headers.index(col_name) if col_name in headers else 0,
        "column_name": col_name,
        "to_harmonize": original_value,
        "top_harmonization": harmonized_value,
        "ontology_id": None,
        "top_harmonizations": [harmonized_value] if harmonized_value else [],
        "match_fidelity": "strong",
        "error": None,
        "row_indices": [row_idx],
        "manual_overrides": [],
    }


def create_manifest_for_file(
    storage: UploadStorage,
    file_id: str,
    original_path: Path,
    changes: dict[int, dict[str, str]],
) -> Path:
    """why: create a manifest parquet for Stage 4 tests in the correct storage location."""
    with original_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        original_rows = list(reader)
        headers = list(reader.fieldnames or [])

    columns_with_changes = _get_columns_with_changes(changes, headers)
    manifest_rows: list[dict[str, Any]] = []

    # Group by (col_name, original_value) to match production manifest structure
    for col_name in columns_with_changes:
        grouped: dict[str, dict[str, Any]] = {}
        for row_idx, original_row in enumerate(original_rows):
            original_value = original_row.get(col_name, "")
            harmonized_value = changes.get(row_idx, {}).get(col_name, original_value)

            if original_value not in grouped:
                grouped[original_value] = {
                    "job_id": f"test-job-{file_id}",
                    "column_id": headers.index(col_name) if col_name in headers else 0,
                    "column_name": col_name,
                    "to_harmonize": original_value,
                    "top_harmonization": harmonized_value,
                    "ontology_id": None,
                    "top_harmonizations": [harmonized_value] if harmonized_value else [],
                    "match_fidelity": "strong",
                    "error": None,
                    "row_indices": [row_idx],
                    "manual_overrides": [],
                }
            else:
                grouped[original_value]["row_indices"].append(row_idx)

        manifest_rows.extend(grouped.values())

    return store_test_harmonization_manifest(storage, file_id, manifest_rows)


def create_manifest_with_manual_override(
    storage: UploadStorage,
    file_id: str,
    original_path: Path,
) -> Path:
    """why: create a manifest with a manual override for testing summary categorization."""
    with original_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        original_rows = list(reader)
        headers = list(reader.fieldnames or [])

    if not headers:
        raise ValueError("CSV must have headers")

    col_name = headers[0]
    original_value = original_rows[0].get(col_name, "") if original_rows else ""
    ai_harmonized_value = "AI Harmonized Value"
    manual_override_value = "User Manual Override"

    manifest_rows: list[dict[str, Any]] = [
        {
            "job_id": f"test-job-{file_id}",
            "column_id": 0,
            "column_name": col_name,
            "to_harmonize": original_value,
            "top_harmonization": ai_harmonized_value,
            "ontology_id": None,
            "top_harmonizations": [ai_harmonized_value],
            "match_fidelity": "partial",
            "error": None,
            "row_indices": [0],
            "manual_overrides": [
                {"user_id": "test-user", "timestamp": "2024-01-01T00:00:00Z", "value": manual_override_value}
            ],
        }
    ]

    return store_test_harmonization_manifest(storage, file_id, manifest_rows)
