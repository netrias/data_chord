"""Shared fixtures for feature-level testing."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.stage_1_upload.services import UploadConstraints, UploadStorage

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
        allowed_content_types=("text/csv", "application/csv", "application/vnd.ms-excel"),
        max_bytes=25 * 1024 * 1024,
    )


@pytest.fixture
def temp_storage(tmp_path: Path, test_constraints: UploadConstraints) -> UploadStorage:
    """why: isolate test file storage from production uploads."""
    return UploadStorage(tmp_path / "uploads", test_constraints)


@pytest.fixture
def mock_netrias_client() -> Generator[MagicMock, None, None]:
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


def _patch_storage(storage: UploadStorage) -> Generator[None, None, None]:
    """why: replace global storage singleton with test instance."""
    with patch("src.stage_1_upload.dependencies._storage", storage):
        with patch("src.stage_1_upload.router._storage", storage):
            with patch("src.stage_1_upload.dependencies.get_upload_storage", return_value=storage):
                yield


@pytest.fixture
async def app_client(
    temp_storage: UploadStorage,
    mock_netrias_client: MagicMock,
) -> AsyncGenerator[AsyncClient, None]:
    """why: provide an async HTTP client for testing the full API."""
    import src.stage_1_upload.dependencies as deps_module
    import src.stage_1_upload.router as router_module
    import src.stage_3_harmonize.router as stage3_router

    original_storage = deps_module._storage
    original_router_storage = router_module._storage
    deps_module._storage = temp_storage
    router_module._storage = temp_storage

    original_get_storage = deps_module.get_upload_storage
    deps_module.get_upload_storage = lambda: temp_storage

    original_stage3_storage = stage3_router._storage
    stage3_router._storage = temp_storage

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


@pytest.fixture
def upload_csv_content() -> bytes:
    """why: provide raw CSV bytes for upload tests."""
    return (FIXTURES_DIR / "sample.csv").read_bytes()


def create_csv_content(rows: list[list[str]]) -> bytes:
    """why: dynamically generate CSV content for specific test scenarios."""
    lines = [",".join(row) for row in rows]
    return "\n".join(lines).encode("utf-8")
