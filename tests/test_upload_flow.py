"""Feature tests for Stage 1 file upload."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from src.stage_1_upload.services import UploadStorage

pytestmark = pytest.mark.asyncio


async def test_valid_csv_upload_returns_file_metadata(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Upload a valid CSV file and verify metadata is returned."""

    # Given
    csv_content = sample_csv_path.read_bytes()
    assert len(csv_content) > 0

    # When
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("test.csv", csv_content, "text/csv")},
    )

    # Then
    assert response.status_code == 201
    data = response.json()
    assert "file_id" in data
    assert data["file_name"] == "test.csv"
    assert data["content_type"] == "text/csv"
    assert "human_size" in data
    assert "uploaded_at" in data


async def test_unsupported_file_type_rejected(app_client: AsyncClient) -> None:
    """Upload with unsupported extension returns 415 error."""

    # Given
    xlsx_content = b"fake xlsx content"

    # When
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("test.xlsx", xlsx_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    # Then
    assert response.status_code == 415
    assert "unsupported" in response.json()["detail"].lower()


async def test_unsupported_content_type_rejected(app_client: AsyncClient) -> None:
    """Upload with unsupported content type returns 415 error."""

    # Given
    json_content = b'{"data": "test"}'

    # When
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("test.json", json_content, "application/json")},
    )

    # Then
    assert response.status_code == 415


async def test_oversized_file_rejected(app_client: AsyncClient) -> None:
    """Upload exceeding size limit returns 413 error."""

    # Given
    oversized_content = b"x" * (26 * 1024 * 1024)

    # When
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("large.csv", oversized_content, "text/csv")},
    )

    # Then
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"].lower()


async def test_metadata_persisted_correctly(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Uploaded file metadata can be loaded from storage."""

    # Given
    csv_content = sample_csv_path.read_bytes()

    # When
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("persist_test.csv", csv_content, "text/csv")},
    )
    file_id = response.json()["file_id"]
    loaded_meta = temp_storage.load(file_id)

    # Then
    assert loaded_meta is not None
    assert loaded_meta.file_id == file_id
    assert loaded_meta.original_name == "persist_test.csv"
    assert loaded_meta.saved_path.exists()
    assert loaded_meta.size_bytes == len(csv_content)


async def test_upload_creates_unique_file_ids(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Multiple uploads generate distinct file IDs."""

    # Given
    csv_content = sample_csv_path.read_bytes()

    # When
    response1 = await app_client.post(
        "/stage-1/upload",
        files={"file": ("first.csv", csv_content, "text/csv")},
    )
    response2 = await app_client.post(
        "/stage-1/upload",
        files={"file": ("second.csv", csv_content, "text/csv")},
    )

    # Then
    assert response1.status_code == 201
    assert response2.status_code == 201
    assert response1.json()["file_id"] != response2.json()["file_id"]
