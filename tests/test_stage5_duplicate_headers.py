"""Feature tests for duplicate-header CSV support through download and row context.

Why: commit eb3ee01 ("enable duplicate CSV headers") migrated column identity to
positional indices, but the CSV read/write path still collapses duplicate headers
via csv.DictReader/DictWriter. These tests exercise the full download round-trip
and Stage 4 row context to prove duplicate-header values round-trip correctly.
"""

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

pytestmark = pytest.mark.asyncio


def _write_positional_csv(path: Path, rows: list[list[str]]) -> None:
    """Bypass DictWriter so duplicate-header fixtures round-trip through disk intact."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerows(rows)


def _write_harmonized_passthrough(saved_path: Path, rows: list[list[str]]) -> Path:
    """Produce a harmonized.csv identical to the upload — download-path test needs no AI changes."""
    harmonized_path = saved_path.with_name(f"{saved_path.stem}{HARMONIZED_SUFFIX}")
    _write_positional_csv(harmonized_path, rows)
    return harmonized_path


def _read_downloaded_csv_positional(response_bytes: bytes) -> tuple[list[str], list[list[str]], bytes]:
    """Return headers, positional rows, and the raw CSV bytes for line-ending checks."""
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_bytes = zf.read(csv_name)
    csv_content = csv_bytes.decode("utf-8")
    reader = csv.reader(io.StringIO(csv_content))
    parsed = list(reader)
    headers, rows = parsed[0], parsed[1:]
    return headers, rows, csv_bytes


async def test_stage5_download_preserves_duplicate_header_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Distinct overrides on duplicate-named columns round-trip to the downloaded CSV."""

    # Given: an uploaded CSV with two columns literally named "sample_id"
    fixture_rows = [
        ["sample_id", "sample_id"],
        ["A", "B"],
        ["C", "D"],
    ]
    file_id = await upload_content(app_client, "\n".join(",".join(r) for r in fixture_rows).encode("utf-8"), "dup.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None

    # And: a passthrough harmonized.csv (no AI changes, just the same duplicate headers)
    _write_harmonized_passthrough(meta.saved_path, fixture_rows)

    # And: overrides addressing each duplicate column by positional column_id
    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            "1": {
                "0": {"ai_value": "A", "human_value": "OVERRIDE_LEFT", "original_value": "A"},
                "1": {"ai_value": "B", "human_value": "OVERRIDE_RIGHT", "original_value": "B"},
            },
        },
        "review_state": review_state_payload(),
    }
    save_response = await app_client.post("/stage-4/overrides", json=overrides_payload)
    assert save_response.status_code == 200

    # Negative assertion: pre-download, the two columns carry distinct values in the fixture
    assert fixture_rows[1][0] != fixture_rows[1][1]

    # When: the download endpoint is invoked
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert response.status_code == 200

    # Then: both duplicate-header columns retain their distinct override values
    headers, rows, csv_bytes = _read_downloaded_csv_positional(response.content)
    assert headers == ["sample_id", "sample_id"]
    assert rows[0] == ["OVERRIDE_LEFT", "OVERRIDE_RIGHT"]
    assert rows[1] == ["C", "D"]

    # And: line endings remain LF (no DictWriter→csv.writer regression)
    assert b"\n" in csv_bytes
    assert b"\r\n" not in csv_bytes


async def test_stage4_row_context_preserves_duplicate_header_values(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Stage 4 row-context endpoint returns distinct values for duplicate-named columns."""

    # Given: an uploaded CSV with two columns named "sample_id" carrying distinct values
    fixture_rows = [
        ["sample_id", "sample_id"],
        ["LEFT_A", "RIGHT_B"],
        ["LEFT_C", "RIGHT_D"],
    ]
    file_id = await upload_content(app_client, "\n".join(",".join(r) for r in fixture_rows).encode("utf-8"), "dup.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None

    # Negative assertion: the fixture itself has distinct values for the two columns
    assert fixture_rows[1][0] != fixture_rows[1][1]
    assert fixture_rows[2][0] != fixture_rows[2][1]

    # When: row-context is requested for both data rows
    response = await app_client.post(
        "/stage-4/row-context",
        json={"file_id": file_id, "row_indices": [0, 1]},
    )
    assert response.status_code == 200

    # Then: duplicate-header columns preserve their positional values
    body = response.json()
    assert body["headers"] == ["sample_id", "sample_id"]
    assert body["rows"] == [["LEFT_A", "RIGHT_B"], ["LEFT_C", "RIGHT_D"]]


async def test_stage5_download_applies_override_to_missing_trailing_cell(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Rows shorter than the header are padded so later-column overrides are not skipped."""

    # Given: a harmonized CSV where the data row omits the trailing "status" cell
    fixture_rows = [
        ["sample_id", "diagnosis", "status"],
        ["S1", "Cancer"],
    ]
    file_id = await upload_content(app_client, b"sample_id,diagnosis,status\nS1,Cancer\n", "ragged.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    _write_harmonized_passthrough(meta.saved_path, fixture_rows)

    # And: a review override targets the missing trailing column by column_id
    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            "1": {
                "2": {"ai_value": None, "human_value": "Reviewed", "original_value": None},
            },
        },
        "review_state": review_state_payload(),
    }
    save_response = await app_client.post("/stage-4/overrides", json=overrides_payload)
    assert save_response.status_code == 200

    # Negative assertion: the fixture row is genuinely shorter than the header before download
    assert len(fixture_rows[1]) < len(fixture_rows[0])

    # When
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert response.status_code == 200

    # Then: the short row is padded and the override lands in the trailing column
    headers, rows, _ = _read_downloaded_csv_positional(response.content)
    assert headers == ["sample_id", "diagnosis", "status"]
    assert rows == [["S1", "Cancer", "Reviewed"]]
