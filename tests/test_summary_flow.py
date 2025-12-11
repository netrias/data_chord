"""Feature tests for Stage 5 results summary."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from src.stage_1_upload.services import UploadStorage
from src.stage_5_review_summary.router import _summarize_differences
from tests.conftest import (
    MAX_EXAMPLES_LIMIT,
    SAMPLE_CSV_COLUMN_COUNT,
    SAMPLE_CSV_ROW_COUNT,
    create_harmonized_csv,
    upload_file,
)


async def test_summary_counts_ai_changes(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Summary correctly counts AI-made changes."""

    # Given
    file_id = await upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {
        0: {"primary_diagnosis": "AI_CHANGE_1"},
        1: {"primary_diagnosis": "AI_CHANGE_2"},
    })

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    assert response.status_code == 200
    data = response.json()
    assert data["ai_changes"] >= 2


async def test_summary_counts_manual_changes(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Summary correctly counts manual changes when specified."""

    # Given
    file_id = await upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {
        0: {"therapeutic_agents": "MANUAL_1"},
        1: {"therapeutic_agents": "MANUAL_2"},
        2: {"therapeutic_agents": "MANUAL_3"},
    })

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": ["therapeutic_agents"]},
    )

    # Then
    data = response.json()
    assert data["manual_changes"] >= 3


async def test_summary_returns_column_summaries(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Summary includes per-column breakdown."""

    # Given
    file_id = await upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    data = response.json()
    assert "column_summaries" in data
    assert len(data["column_summaries"]) > 0
    for summary in data["column_summaries"]:
        assert "column" in summary
        assert "ai_changes" in summary
        assert "manual_changes" in summary
        assert "unchanged" in summary


async def test_summary_limits_examples_to_20(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """AI and manual examples are limited to 20 each."""

    # Given
    file_id = await upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    changes = {i: {"primary_diagnosis": f"CHANGE_{i}"} for i in range(10)}
    create_harmonized_csv(meta.saved_path, changes)

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    data = response.json()
    assert len(data["ai_examples"]) <= MAX_EXAMPLES_LIMIT
    assert len(data["manual_examples"]) <= MAX_EXAMPLES_LIMIT


async def test_summary_file_not_found_returns_404(app_client: AsyncClient) -> None:
    """Request with invalid file_id returns 404."""

    # Given
    invalid_file_id = "nonexistent123"

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": invalid_file_id, "manual_columns": []},
    )

    # Then
    assert response.status_code == 404


async def test_summary_harmonized_missing_returns_404(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Request without harmonized file returns 404."""

    # Given
    file_id = await upload_file(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    assert response.status_code == 404


async def test_summary_returns_total_rows(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Summary includes total row count."""

    # Given
    file_id = await upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    data = response.json()
    assert data["total_rows"] == SAMPLE_CSV_ROW_COUNT


async def test_summary_returns_columns_reviewed_count(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Summary includes count of columns reviewed."""

    # Given
    file_id = await upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(meta.saved_path, {})

    # When
    response = await app_client.post(
        "/stage-5/summary",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    data = response.json()
    assert data["columns_reviewed"] == SAMPLE_CSV_COLUMN_COUNT


def test_summarize_differences_unchanged_rows() -> None:
    """Direct function test: unchanged rows counted correctly."""

    # Given
    headers = ["col1", "col2"]
    original_rows = [
        {"col1": "a", "col2": "b"},
        {"col1": "c", "col2": "d"},
    ]
    harmonized_rows = [
        {"col1": "a", "col2": "b"},
        {"col1": "c", "col2": "d"},
    ]

    # When
    result = _summarize_differences(headers, original_rows, harmonized_rows, [])

    # Then
    assert result.ai_changes == 0
    assert result.manual_changes == 0
    total_unchanged = sum(s.unchanged for s in result.column_summaries)
    assert total_unchanged == 4


def test_summarize_differences_ai_changes() -> None:
    """Direct function test: AI changes counted when no manual columns specified."""

    # Given
    headers = ["col1"]
    original_rows = [{"col1": "original"}]
    harmonized_rows = [{"col1": "changed"}]

    # When
    result = _summarize_differences(headers, original_rows, harmonized_rows, [])

    # Then
    assert result.ai_changes == 1
    assert result.manual_changes == 0


def test_summarize_differences_manual_changes() -> None:
    """Direct function test: changes in manual columns counted as manual."""

    # Given
    headers = ["col1"]
    original_rows = [{"col1": "original"}]
    harmonized_rows = [{"col1": "changed"}]

    # When
    result = _summarize_differences(headers, original_rows, harmonized_rows, ["col1"])

    # Then
    assert result.ai_changes == 0
    assert result.manual_changes == 1
