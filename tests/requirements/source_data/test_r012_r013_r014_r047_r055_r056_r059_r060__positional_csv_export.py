"""Requirement tests for positional CSV behavior through review and export."""

from __future__ import annotations

import csv
import io
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from httpx import AsyncClient

from src.domain.storage import HARMONIZED_SUFFIX, UploadStorage
from tests.conftest import review_state_payload, upload_content
from tests.requirements.helpers import (
    CANONICAL_DIAGNOSIS,
    LEFT_DUPLICATE_VALUE,
    LEFT_DUPLICATE_VALUE_2,
    LEFT_OVERRIDE_VALUE,
    RIGHT_DUPLICATE_VALUE,
    RIGHT_DUPLICATE_VALUE_2,
    RIGHT_OVERRIDE_VALUE,
    SAMPLE_ID_COLUMN,
    STATUS_COLUMN,
)

pytestmark = pytest.mark.asyncio


def _write_positional_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerows(rows)


def _write_harmonized_passthrough(saved_path: Path, rows: list[list[str]]) -> Path:
    harmonized_path = saved_path.with_name(f"{saved_path.stem}{HARMONIZED_SUFFIX}")
    _write_positional_csv(harmonized_path, rows)
    return harmonized_path


def _read_downloaded_csv_positional(response_bytes: bytes) -> tuple[list[str], list[list[str]], bytes]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_bytes = zf.read(csv_name)
    reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
    parsed = list(reader)
    return parsed[0], parsed[1:], csv_bytes


@pytest.mark.requirements("R-012", "R-013", "R-047")
async def test_r012_r013_r047__row_context_preserves_duplicate_header_positions(
    app_client: AsyncClient,
) -> None:
    """
    Given: A CSV has two columns with the same header and distinct positional values.
    When: Stage 4 row context is requested for those rows.
    Then: The response preserves both duplicate headers and each positional cell value.
    """
    # Given
    fixture_rows = [
        [SAMPLE_ID_COLUMN, SAMPLE_ID_COLUMN],
        [LEFT_DUPLICATE_VALUE, RIGHT_DUPLICATE_VALUE],
        [LEFT_DUPLICATE_VALUE_2, RIGHT_DUPLICATE_VALUE_2],
    ]
    file_id = await upload_content(app_client, "\n".join(",".join(r) for r in fixture_rows).encode(), "dup.csv")
    assert fixture_rows[1][0] != fixture_rows[1][1]

    # When
    response = await app_client.post(
        "/stage-4/row-context",
        json={"file_id": file_id, "row_indices": [0, 1]},
    )

    # Then
    assert response.status_code == 200
    body = response.json()
    assert body["headers"] == [SAMPLE_ID_COLUMN, SAMPLE_ID_COLUMN]
    assert body["rows"] == [
        [LEFT_DUPLICATE_VALUE, RIGHT_DUPLICATE_VALUE],
        [LEFT_DUPLICATE_VALUE_2, RIGHT_DUPLICATE_VALUE_2],
    ]


@pytest.mark.requirements("R-005", "R-012", "R-013", "R-046", "R-055", "R-056", "R-060")
async def test_r005_r012_r013_r046_r055_r056_r060__export_applies_overrides_to_duplicate_header_positions(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: Saved overrides address two duplicate-named columns by positional column id.
    When: Stage 5 export downloads the harmonized CSV.
    Then: Each override lands in its positional column and duplicate headers are preserved.
    """
    # Given
    fixture_rows = [
        [SAMPLE_ID_COLUMN, SAMPLE_ID_COLUMN],
        ["A", "B"],
        ["C", "D"],
    ]
    file_id = await upload_content(app_client, "\n".join(",".join(r) for r in fixture_rows).encode(), "dup.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    _write_harmonized_passthrough(meta.saved_path, fixture_rows)
    assert fixture_rows[1][0] != fixture_rows[1][1]

    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {
                    "0": {"ai_value": "A", "human_value": LEFT_OVERRIDE_VALUE, "original_value": "A"},
                    "1": {"ai_value": "B", "human_value": RIGHT_OVERRIDE_VALUE, "original_value": "B"},
                },
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # When
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    headers, rows, csv_bytes = _read_downloaded_csv_positional(response.content)
    assert headers == [SAMPLE_ID_COLUMN, SAMPLE_ID_COLUMN]
    assert rows[0] == [LEFT_OVERRIDE_VALUE, RIGHT_OVERRIDE_VALUE]
    assert rows[1] == ["C", "D"]
    assert b"\n" in csv_bytes
    assert b"\r\n" not in csv_bytes


@pytest.mark.requirements("R-014", "R-059")
async def test_r014_r059__export_pads_short_rows_before_applying_trailing_override(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A harmonized CSV row is shorter than the header and has a saved trailing override.
    When: Stage 5 export downloads the harmonized CSV.
    Then: The row is padded and the override appears in the intended trailing column.
    """
    # Given
    fixture_rows = [
        [SAMPLE_ID_COLUMN, "diagnosis", STATUS_COLUMN],
        ["S1", CANONICAL_DIAGNOSIS],
    ]
    file_id = await upload_content(app_client, b"sample_id,diagnosis,status\nS1,Melanoma\n", "ragged.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    _write_harmonized_passthrough(meta.saved_path, fixture_rows)
    assert len(fixture_rows[1]) < len(fixture_rows[0])

    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {
                    "2": {"ai_value": None, "human_value": "Reviewed", "original_value": None},
                },
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    # When
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    headers, rows, _ = _read_downloaded_csv_positional(response.content)
    assert headers == [SAMPLE_ID_COLUMN, "diagnosis", STATUS_COLUMN]
    assert rows == [["S1", CANONICAL_DIAGNOSIS, "Reviewed"]]
