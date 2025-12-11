"""Feature tests for Stage 1 column analysis and CDE discovery."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from src.stage_1_upload.services import UploadStorage
from tests.conftest import create_csv_content

pytestmark = pytest.mark.asyncio


async def _upload_file(client: AsyncClient, csv_path: Path) -> str:
    """why: helper to upload a file and return its file_id."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (csv_path.name, csv_path.read_bytes(), "text/csv")},
    )
    return response.json()["file_id"]


async def _upload_content(client: AsyncClient, content: bytes, filename: str = "test.csv") -> str:
    """why: helper to upload raw content and return its file_id."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (filename, content, "text/csv")},
    )
    return response.json()["file_id"]


async def test_column_detection_matches_headers(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Analyze returns columns matching CSV headers."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    assert response.status_code == 200
    data = response.json()
    column_names = [col["column_name"] for col in data["columns"]]
    expected_headers = [
        "record_id",
        "therapeutic_agents",
        "primary_diagnosis",
        "morphology",
        "tissue_or_organ_of_origin",
        "sample_anatomic_site",
    ]
    assert column_names == expected_headers


async def test_numeric_type_inference(app_client: AsyncClient, types_csv_path: Path) -> None:
    """Numeric columns are detected as 'numeric' type."""

    # Given
    file_id = await _upload_file(app_client, types_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    numeric_col = next(col for col in data["columns"] if col["column_name"] == "numeric_col")
    assert numeric_col["inferred_type"] == "numeric"


async def test_date_type_inference(app_client: AsyncClient, types_csv_path: Path) -> None:
    """Date columns are detected as 'date' type."""

    # Given
    file_id = await _upload_file(app_client, types_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    date_col = next(col for col in data["columns"] if col["column_name"] == "date_col")
    assert date_col["inferred_type"] == "date"


async def test_text_type_fallback(app_client: AsyncClient, types_csv_path: Path) -> None:
    """Mixed content columns fall back to 'text' type."""

    # Given
    file_id = await _upload_file(app_client, types_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    text_col = next(col for col in data["columns"] if col["column_name"] == "text_col")
    assert text_col["inferred_type"] == "text"


async def test_confidence_high_for_full_column(
    app_client: AsyncClient,
    with_nulls_csv_path: Path,
) -> None:
    """Column with all non-null values gets 'high' confidence."""

    # Given
    file_id = await _upload_file(app_client, with_nulls_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    high_col = next(col for col in data["columns"] if col["column_name"] == "high_confidence")
    assert high_col["confidence_bucket"] == "high"


async def test_confidence_medium_for_partial_column(
    app_client: AsyncClient,
    with_nulls_csv_path: Path,
) -> None:
    """Column with 50-80% non-null values gets 'medium' confidence."""

    # Given
    file_id = await _upload_file(app_client, with_nulls_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    medium_col = next(col for col in data["columns"] if col["column_name"] == "medium_confidence")
    assert medium_col["confidence_bucket"] == "medium"


async def test_confidence_low_for_sparse_column(
    app_client: AsyncClient,
    with_nulls_csv_path: Path,
) -> None:
    """Column with <50% non-null values gets 'low' confidence."""

    # Given
    file_id = await _upload_file(app_client, with_nulls_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    low_col = next(col for col in data["columns"] if col["column_name"] == "low_confidence")
    assert low_col["confidence_bucket"] == "low"


async def test_sample_values_truncated_at_80_chars(app_client: AsyncClient) -> None:
    """Long values in sample_values are truncated to 80 characters."""

    # Given
    long_value = "x" * 100
    content = create_csv_content([
        ["long_col"],
        [long_value],
        [long_value],
        [long_value],
        [long_value],
        [long_value],
    ])
    file_id = await _upload_content(app_client, content)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    long_col = next(col for col in data["columns"] if col["column_name"] == "long_col")
    for sample in long_col["sample_values"]:
        assert len(sample) <= 80


async def test_cde_suggestions_returned_with_mock(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Analyze returns CDE target suggestions from mocked Netrias client."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    assert "cde_targets" in data
    assert isinstance(data["cde_targets"], dict)


async def test_manifest_saved_after_analyze(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Analyze saves a manifest file for use in later stages."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    assert response.status_code == 200
    manifest = temp_storage.load_manifest(file_id)
    assert manifest is not None
    assert "column_mappings" in manifest


async def test_analyze_returns_total_rows(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Analyze response includes accurate total row count."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "target_schema": "CCDI"},
    )

    # Then
    data = response.json()
    assert data["total_rows"] == 10


async def test_analyze_with_invalid_file_id_returns_404(app_client: AsyncClient) -> None:
    """Analyze with non-existent file_id returns 404."""

    # Given
    invalid_file_id = "deadbeef12345678deadbeef12345678"

    # When
    response = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": invalid_file_id, "target_schema": "CCDI"},
    )

    # Then
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
