"""Feature tests for Stage 4 batch row assembly."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from src.stage_1_upload.services import UploadStorage
from src.stage_4_review_results.router import _build_rows, _summarize_record_ids


def _create_harmonized_csv(original_path: Path, changes: dict[int, dict[str, str]]) -> Path:
    """why: create a .harmonized.csv alongside the original with specified changes."""
    import csv

    with original_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []

    for row_idx, column_changes in changes.items():
        if row_idx < len(rows):
            rows[row_idx].update(column_changes)

    harmonized_path = original_path.with_name(f"{original_path.stem}.harmonized.csv")
    with harmonized_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    return harmonized_path


async def _upload_file(client: AsyncClient, csv_path: Path) -> str:
    """why: helper to upload a file and return its file_id."""
    response = await client.post(
        "/stage-1/upload",
        files={"file": (csv_path.name, csv_path.read_bytes(), "text/csv")},
    )
    return response.json()["file_id"]


async def test_rows_returned_with_harmonized_file(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Stage 4 returns rows when harmonized file exists."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    _create_harmonized_csv(meta.saved_path, {})

    # When
    response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    assert response.status_code == 200
    data = response.json()
    assert "rows" in data
    assert len(data["rows"]) > 0


async def test_unchanged_row_has_high_confidence(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Rows with no changes have 'high' confidence bucket."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    _create_harmonized_csv(meta.saved_path, {})

    # When
    response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    data = response.json()
    first_row = data["rows"][0]
    for cell in first_row["cells"]:
        if not cell["isChanged"]:
            assert cell["bucket"] == "high"


async def test_changed_row_has_low_confidence(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Rows with changes have 'low' confidence bucket."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    _create_harmonized_csv(meta.saved_path, {0: {"primary_diagnosis": "CHANGED_VALUE"}})

    # When
    response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    data = response.json()
    found_changed = False
    for row in data["rows"]:
        for cell in row["cells"]:
            if cell["isChanged"]:
                assert cell["bucket"] == "low"
                found_changed = True
    assert found_changed


async def test_manual_column_gets_lower_confidence(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    sample_csv_path: Path,
) -> None:
    """Changed cells in manual columns get confidence 0.2."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)
    meta = temp_storage.load(file_id)
    assert meta is not None
    _create_harmonized_csv(meta.saved_path, {0: {"primary_diagnosis": "MANUAL_CHANGE"}})

    # When
    response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": ["primary_diagnosis"]},
    )

    # Then
    data = response.json()
    found_manual = False
    for row in data["rows"]:
        for cell in row["cells"]:
            if cell["columnKey"] == "primary_diagnosis" and cell["isChanged"]:
                assert cell["confidence"] == 0.2
                found_manual = True
    assert found_manual


async def test_file_not_found_returns_404(app_client: AsyncClient) -> None:
    """Request with invalid file_id returns 404."""

    # Given
    invalid_file_id = "nonexistent123"

    # When
    response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": invalid_file_id, "manual_columns": []},
    )

    # Then
    assert response.status_code == 404


async def test_harmonized_missing_returns_404(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    """Request without harmonized file returns 404."""

    # Given
    file_id = await _upload_file(app_client, sample_csv_path)

    # When
    response = await app_client.post(
        "/stage-4/rows",
        json={"file_id": file_id, "manual_columns": []},
    )

    # Then
    assert response.status_code == 404
    assert "harmonized" in response.json()["detail"].lower()


def test_summarize_record_ids_single() -> None:
    """Single record ID returns as-is."""

    # Given
    record_ids = ["R001"]

    # When
    result = _summarize_record_ids(record_ids)

    # Then
    assert result == "R001"


def test_summarize_record_ids_multiple() -> None:
    """Multiple record IDs show first plus count."""

    # Given
    record_ids = ["R001", "R002", "R003"]

    # When
    result = _summarize_record_ids(record_ids)

    # Then
    assert result == "R001 + 2 more"


def test_summarize_record_ids_empty() -> None:
    """Empty list returns fallback message."""

    # Given
    record_ids: list[str] = []

    # When
    result = _summarize_record_ids(record_ids)

    # Then
    assert result == "Multiple records"


def test_build_rows_groups_identical_rows() -> None:
    """Identical rows are grouped together."""

    # Given
    headers = ["therapeutic_agents", "primary_diagnosis", "morphology", "tissue_or_organ_of_origin", "sample_anatomic_site"]
    original_rows = [
        {"record_id": "R001", "therapeutic_agents": "Aspirin", "primary_diagnosis": "Cancer", "morphology": "A", "tissue_or_organ_of_origin": "Lung", "sample_anatomic_site": "Left"},
        {"record_id": "R002", "therapeutic_agents": "Aspirin", "primary_diagnosis": "Cancer", "morphology": "A", "tissue_or_organ_of_origin": "Lung", "sample_anatomic_site": "Left"},
    ]
    harmonized_rows = [
        {"record_id": "R001", "therapeutic_agents": "Aspirin", "primary_diagnosis": "Cancer", "morphology": "A", "tissue_or_organ_of_origin": "Lung", "sample_anatomic_site": "Left"},
        {"record_id": "R002", "therapeutic_agents": "Aspirin", "primary_diagnosis": "Cancer", "morphology": "A", "tissue_or_organ_of_origin": "Lung", "sample_anatomic_site": "Left"},
    ]

    # When
    rows = _build_rows(headers, original_rows, harmonized_rows, [])

    # Then
    assert len(rows) == 1
    assert "R001 + 1 more" in rows[0].recordId or rows[0].recordId == "R001 + 1 more"


def test_build_rows_preserves_source_row_number() -> None:
    """Built rows include the source row number."""

    # Given
    headers = ["therapeutic_agents", "primary_diagnosis", "morphology", "tissue_or_organ_of_origin", "sample_anatomic_site"]
    original_rows = [
        {"record_id": "R001", "therapeutic_agents": "A", "primary_diagnosis": "B", "morphology": "C", "tissue_or_organ_of_origin": "D", "sample_anatomic_site": "E"},
    ]
    harmonized_rows = [
        {"record_id": "R001", "therapeutic_agents": "A", "primary_diagnosis": "B", "morphology": "C", "tissue_or_organ_of_origin": "D", "sample_anatomic_site": "E"},
    ]

    # When
    rows = _build_rows(headers, original_rows, harmonized_rows, [])

    # Then
    assert rows[0].sourceRowNumber == 1
