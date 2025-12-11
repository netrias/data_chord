"""Shared fixtures for feature-level testing."""

from __future__ import annotations

import csv
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.stage_1_upload.services import UploadConstraints, UploadStorage

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Test constants
TEST_CSV_CONTENT_TYPE = "text/csv"
TEST_TARGET_SCHEMA = "CCDI"
HARMONIZED_SUFFIX = ".harmonized.csv"
SAMPLE_CSV_ROW_COUNT = 10
SAMPLE_CSV_COLUMN_COUNT = 6
MAX_EXAMPLES_LIMIT = 20
MANUAL_COLUMN_CONFIDENCE = 0.2


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
    """why: avoid real network calls and control mapping responses."""
    mock_client = MagicMock()

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
        raw={
            "recognized_mappings": {"primary_diagnosis": 2, "therapeutic_agents": 1},
            "column_mappings": {
                "primary_diagnosis": {
                    "targetField": "primary_diagnosis",
                    "cdeId": 2,
                },
                "therapeutic_agents": {
                    "targetField": "therapeutic_agents",
                    "cdeId": 1,
                },
            },
        },
    )

    mock_client.harmonize.return_value = MockHarmonizeResult(
        status="succeeded",
        description="Harmonization completed.",
        job_id="mock-job-id-12345",
    )

    with (
        patch("src.stage_1_upload.mapping_service.NetriasClient", return_value=mock_client),
        patch("src.stage_1_upload.harmonize.NetriasClient", return_value=mock_client),
    ):
        yield mock_client


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
    import src.stage_1_upload.dependencies as deps_module
    import src.stage_1_upload.router as router_module
    import src.stage_3_harmonize.router as stage3_router
    import src.stage_4_review_results.router as stage4_router
    import src.stage_5_review_summary.router as stage5_router

    original_storage = deps_module._storage
    original_router_storage = router_module._storage
    deps_module._storage = temp_storage
    router_module._storage = temp_storage

    original_get_storage = deps_module.get_upload_storage
    deps_module.get_upload_storage = lambda: temp_storage

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
        stage3_router._storage = original_stage3_storage
        stage4_router.get_upload_storage = original_stage4_get_storage
        stage5_router.get_upload_storage = original_stage5_get_storage


@pytest.fixture
def upload_csv_content() -> bytes:
    """why: provide raw CSV bytes for upload tests."""
    return (FIXTURES_DIR / "sample.csv").read_bytes()


def create_csv_content(rows: list[list[str]]) -> bytes:
    """why: dynamically generate CSV content for specific test scenarios."""
    lines = [",".join(row) for row in rows]
    return "\n".join(lines).encode("utf-8")


async def upload_file(client: AsyncClient, csv_path: Path) -> str:
    """why: upload a file and return its file_id for use in subsequent test steps."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (csv_path.name, csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    return response.json()["file_id"]


async def upload_content(client: AsyncClient, content: bytes, filename: str = "test.csv") -> str:
    """why: upload raw content and return its file_id for dynamic test scenarios."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (filename, content, TEST_CSV_CONTENT_TYPE)},
    )
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
    with original_path.open("r", newline="", encoding="utf-8") as f:
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
