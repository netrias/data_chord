"""Provide shared fixtures for feature-level testing."""

from __future__ import annotations

import csv
import os
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from httpx import ASGITransport, AsyncClient

from src.domain.storage import HARMONIZED_SUFFIX, UploadConstraints, UploadStorage

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Test constants
TEST_CSV_CONTENT_TYPE = "text/csv"
TEST_TARGET_SCHEMA = "CCDI"
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
            "completed_units": [],
            "flagged_units": [],
            "batch_size": 5,
        },
        "row_mode": {
            "current_unit": 1,
            "completed_units": [],
            "flagged_units": [],
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


@pytest.fixture
def test_constraints() -> UploadConstraints:
    """why: provide smaller limits for faster test execution."""
    return UploadConstraints(
        allowed_suffixes=(".csv",),
        allowed_content_types=(TEST_CSV_CONTENT_TYPE, "application/csv", "application/vnd.ms-excel"),
        max_bytes=25 * 1024 * 1024,
    )


@pytest.fixture
def temp_storage(tmp_path: Path, test_constraints: UploadConstraints) -> UploadStorage:
    """why: isolate test file storage from production uploads."""
    return UploadStorage(tmp_path / "uploads", test_constraints)


@pytest.fixture
def mock_netrias_client() -> Generator[MagicMock]:
    """why: avoid real network calls to Netrias Lambda and Data Model Store APIs."""
    import src.domain.dependencies as deps

    mock_client = MagicMock()

    _cde_manifest = {
        "column_mappings": {
            "primary_diagnosis": {
                "targetField": "primary_diagnosis",
                "cde_id": 2,
            },
            "therapeutic_agents": {
                "targetField": "therapeutic_agents",
                "cde_id": 1,
            },
        },
    }

    mock_client.discover_cde_mapping.return_value = MockCDEMappingResult(
        suggestions=[
            MockMappingSuggestion(
                source_column="primary_diagnosis",
                options=[MockMappingOption(target="primary_diagnosis", confidence=0.95)],
            ),
            MockMappingSuggestion(
                source_column="therapeutic_agents",
                options=[MockMappingOption(target="therapeutic_agents", confidence=0.90)],
            ),
        ],
        raw={"recognized_mappings": {"primary_diagnosis": 2, "therapeutic_agents": 1}, **_cde_manifest},
    )

    # MappingDiscoveryService.discover() calls this after demo_bypass removal
    mock_client.discover_mapping_from_csv.return_value = _cde_manifest
    mock_client.configure.return_value = None

    mock_client.harmonize.return_value = MockHarmonizeResult(
        status="succeeded",
        description="Harmonization completed.",
        job_id="mock-job-id-12345",
    )

    # DMS methods on the shared NetriasClient mock
    from netrias_client import CDE as SdkCDE
    from netrias_client import DataModel, DataModelVersion

    mock_client.list_data_models.return_value = (
        DataModel(
            data_commons_id=1, key="test-data-model", name="Test Data Model",
            description=None, is_active=True,
            versions=(DataModelVersion(version_label="1"),),
        ),
    )
    mock_client.list_cdes.return_value = (
        SdkCDE(cde_key="primary_diagnosis", cde_id=2, cde_version_id=1, description="Primary Diagnosis"),
        SdkCDE(cde_key="therapeutic_agents", cde_id=1, cde_version_id=1, description="Therapeutic Agents"),
    )
    mock_client.get_pv_set.return_value = frozenset()
    mock_client.get_pv_set_async.return_value = frozenset()

    # Reset dependency singletons so the mock is injected
    saved_client = deps._netrias_client
    saved_init = deps._netrias_client_initialized
    saved_mapping = deps._mapping_discovery
    saved_harmonizer = deps._harmonizer
    deps._netrias_client = mock_client
    deps._netrias_client_initialized = True
    deps._mapping_discovery = None
    deps._harmonizer = None

    with patch.dict(os.environ, {"NETRIAS_API_KEY": "test-api-key"}):
        yield mock_client

    # Restore singletons to avoid leaking mock state
    deps._netrias_client = saved_client
    deps._netrias_client_initialized = saved_init
    deps._mapping_discovery = saved_mapping
    deps._harmonizer = saved_harmonizer


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
) -> AsyncGenerator[AsyncClient]:
    """why: provide an async HTTP client for testing the full API."""
    import src.domain.dependencies as deps_module
    import src.stage_1_upload.router as router_module
    import src.stage_3_harmonize.router as stage3_router
    import src.stage_4_review_results.router as stage4_router
    import src.stage_5_review_summary.router as stage5_router
    from src.domain.storage import FileStore, LocalStorageBackend

    original_storage = deps_module._storage
    original_router_storage = router_module._storage
    deps_module._storage = temp_storage
    router_module._storage = temp_storage

    original_get_storage = deps_module.get_upload_storage
    deps_module.get_upload_storage = lambda: temp_storage

    original_get_file_store = deps_module.get_file_store
    test_store = FileStore(LocalStorageBackend(temp_storage._base_dir / "manifests"))
    deps_module.get_file_store = lambda: test_store

    original_stage3_storage = stage3_router._storage
    stage3_router._storage = temp_storage

    # Patch stage 4 and 5 to use test storage via get_upload_storage
    original_stage4_get_storage = stage4_router.get_upload_storage
    stage4_router.get_upload_storage = lambda: temp_storage

    original_stage5_get_storage = stage5_router.get_upload_storage
    stage5_router.get_upload_storage = lambda: temp_storage

    try:
        from backend.app.main import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        deps_module._storage = original_storage
        router_module._storage = original_router_storage
        deps_module.get_upload_storage = original_get_storage
        deps_module.get_file_store = original_get_file_store
        stage3_router._storage = original_stage3_storage
        stage4_router.get_upload_storage = original_stage4_get_storage
        stage5_router.get_upload_storage = original_stage5_get_storage


@pytest.fixture
def upload_csv_content() -> bytes:
    """why: provide raw CSV bytes for upload tests."""
    return (FIXTURES_DIR / "sample.csv").read_bytes()


def create_csv_content(rows: list[list[str]]) -> bytes:
    """why: dynamically generate CSV content for specific test scenarios."""
    lines: list[str] = [",".join(row) for row in rows]
    return "\n".join(lines).encode("utf-8")


async def upload_file(client: AsyncClient, csv_path: Path) -> str:
    """why: upload a file and return its file_id for use in subsequent test steps."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (csv_path.name, csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    assert response.status_code == 201, f"Upload failed: {response.status_code} {response.text}"
    return response.json()["file_id"]


async def upload_content(client: AsyncClient, content: bytes, filename: str = "test.csv") -> str:
    """why: upload raw content and return its file_id for dynamic test scenarios."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (filename, content, TEST_CSV_CONTENT_TYPE)},
    )
    assert response.status_code == 201, f"Upload failed: {response.status_code} {response.text}"
    return response.json()["file_id"]


async def upload_and_analyze(client: AsyncClient, csv_path: Path) -> str:
    """why: upload and analyze a file, returning file_id for harmonization tests."""
    file_id = await upload_file(client, csv_path)
    await client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )
    return file_id


def create_harmonized_csv(original_path: Path, changes: dict[int, dict[str, str]]) -> Path:
    """why: create a .harmonized.csv alongside the original with specified changes."""
    with original_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []

    for row_idx, column_changes in changes.items():
        if row_idx < len(rows):
            rows[row_idx].update(column_changes)

    harmonized_path = original_path.with_name(f"{original_path.stem}{HARMONIZED_SUFFIX}")
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
    from src.domain.manifest import get_manifest_schema

    schema = get_manifest_schema()

    arrays = {
        "job_id": [row.get("job_id", "test-job") for row in rows],
        "column_id": [row.get("column_id", 0) for row in rows],
        "column_name": [row.get("column_name", "") for row in rows],
        "to_harmonize": [row.get("to_harmonize", "") for row in rows],
        "top_harmonization": [row.get("top_harmonization", "") for row in rows],
        "ontology_id": [row.get("ontology_id") for row in rows],
        "top_harmonizations": [row.get("top_harmonizations", []) for row in rows],
        "confidence_score": [row.get("confidence_score") for row in rows],
        "error": [row.get("error") for row in rows],
        "row_indices": [row.get("row_indices", []) for row in rows],
        "manual_overrides": [row.get("manual_overrides", []) for row in rows],
    }

    table = pa.table(arrays, schema=schema)
    pq.write_table(table, output_path)
    return output_path


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
        "confidence_score": 0.95 if original_value != harmonized_value else 0.99,
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
                    "confidence_score": 0.95 if original_value != harmonized_value else 0.99,
                    "error": None,
                    "row_indices": [row_idx],
                    "manual_overrides": [],
                }
            else:
                grouped[original_value]["row_indices"].append(row_idx)

        manifest_rows.extend(grouped.values())

    manifest_dir = storage.manifest_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{file_id}_harmonization.parquet"
    return create_test_manifest_parquet(manifest_path, manifest_rows)


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

    manifest_rows: list[dict[str, Any]] = [{
        "job_id": f"test-job-{file_id}",
        "column_id": 0,
        "column_name": col_name,
        "to_harmonize": original_value,
        "top_harmonization": ai_harmonized_value,
        "ontology_id": None,
        "top_harmonizations": [ai_harmonized_value],
        "confidence_score": 0.85,
        "error": None,
        "row_indices": [0],
        "manual_overrides": [
            {"user_id": "test-user", "timestamp": "2024-01-01T00:00:00Z", "value": manual_override_value}
        ],
    }]

    manifest_dir = storage.manifest_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{file_id}_harmonization.parquet"
    return create_test_manifest_parquet(manifest_path, manifest_rows)
