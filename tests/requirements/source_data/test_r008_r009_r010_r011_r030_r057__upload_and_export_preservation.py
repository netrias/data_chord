"""Requirement tests for user-level upload and untouched export preservation."""

from __future__ import annotations

import csv
import io
import zipfile
from io import BytesIO

import pytest
from httpx import AsyncClient

from src.domain.storage import HARMONIZED_SUFFIX, UploadStorage
from tests.conftest import upload_content
from tests.requirements.helpers import (
    CANONICAL_LUNG_CANCER,
    DIAGNOSIS_COLUMN,
    NOTES_COLUMN,
    WHITESPACE_LUNG_CANCER,
    WHITESPACE_UNTOUCHED_NOTE,
)

pytestmark = pytest.mark.asyncio


def _read_downloaded_csv(response_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")
    parsed = list(csv.reader(io.StringIO(csv_content)))
    return parsed[0], parsed[1:]


@pytest.mark.requirements("R-008", "R-009")
async def test_r008_r009__csv_upload_returns_stable_file_id(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A user has CSV content ready to upload.
    When: The user uploads the CSV through Stage 1.
    Then: The response includes a stable file id that loads the stored upload.
    """
    # Given
    content = f"{DIAGNOSIS_COLUMN}\n{CANONICAL_LUNG_CANCER}\n".encode()
    assert temp_storage.load("not-yet-created") is None

    # When
    file_id = await upload_content(app_client, content, "source.csv")

    # Then
    meta = temp_storage.load(file_id)
    assert meta is not None
    assert meta.file_id == file_id
    assert meta.original_name == "source.csv"


@pytest.mark.requirements("R-010", "R-011", "R-030", "R-053", "R-057")
async def test_r010_r011_r030_r053_r057__untouched_source_value_whitespace_survives_export(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A user uploads a CSV with leading and trailing whitespace in an untouched cell.
    When: The user downloads the Stage 5 CSV for that workflow.
    Then: The exported CSV preserves the untouched cell value exactly.
    """
    # Given
    content = f"{DIAGNOSIS_COLUMN},{NOTES_COLUMN}\n{WHITESPACE_LUNG_CANCER},{WHITESPACE_UNTOUCHED_NOTE}\n".encode()
    file_id = await upload_content(app_client, content, "whitespace.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    harmonized_path = meta.saved_path.with_name(f"{meta.saved_path.stem}{HARMONIZED_SUFFIX}")
    harmonized_path.write_bytes(content)
    assert WHITESPACE_LUNG_CANCER.encode() in content

    # When
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    headers, rows = _read_downloaded_csv(response.content)
    assert headers == [DIAGNOSIS_COLUMN, NOTES_COLUMN]
    assert rows == [[WHITESPACE_LUNG_CANCER, WHITESPACE_UNTOUCHED_NOTE]]
