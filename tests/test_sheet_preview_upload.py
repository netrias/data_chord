"""Stage 1 upload response sheet preview tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from httpx import AsyncClient

from src.stage_1_upload.services import read_workbook_sheet_previews
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_XLSX_CONTENT_TYPE,
    create_csv_content,
    create_xlsx_content,
)


def test_read_workbook_sheet_previews_preserves_values_and_shapes_rows(tmp_path: Path) -> None:
    """Preview values are exact cell strings shaped to the header width."""

    # Given: a workbook with significant whitespace, typed values, and ragged data rows
    workbook_path = tmp_path / "preview.xlsx"
    saved_at = datetime(2024, 1, 1, 9, 30)
    workbook_path.write_bytes(
        create_xlsx_content(
            {
                "Patients": [
                    ["  Patient ID  ", "Age", "Seen At"],
                    ["  A  ", 42, saved_at],
                    ["short"],
                    ["too", "many", "columns", "ignored"],
                ],
            }
        )
    )
    assert read_workbook_sheet_previews(tmp_path / "preview.csv", ["Patients"]) == {}

    # When: previews are read from the workbook
    previews = read_workbook_sheet_previews(workbook_path, ["Patients"], max_rows=3, max_cols=3)

    # Then: cell text is preserved and rows match the header width
    preview = previews["Patients"]
    assert preview.headers == ["  Patient ID  ", "Age", "Seen At"]
    assert preview.rows == [
        ["  A  ", "42", str(saved_at)],
        ["short", "", ""],
        ["too", "many", "columns"],
    ]
    assert preview.truncated_rows is False
    assert preview.truncated_columns is True


def test_read_workbook_sheet_previews_sets_truncation_flags(tmp_path: Path) -> None:
    """Preview reads one extra row and column to detect hidden data."""

    # Given: a workbook larger than the preview caps
    workbook_path = tmp_path / "wide-tall.xlsx"
    workbook_path.write_bytes(
        create_xlsx_content(
            {
                "WideTall": [
                    ["h1", "h2", "h3"],
                    ["r1c1", "r1c2", "r1c3"],
                    ["r2c1", "r2c2", "r2c3"],
                    ["r3c1", "r3c2", "r3c3"],
                ],
            }
        )
    )
    full_preview = read_workbook_sheet_previews(
        workbook_path,
        ["WideTall"],
        max_rows=3,
        max_cols=3,
    )["WideTall"]
    assert full_preview.truncated_rows is False

    # When: lower preview caps are used
    preview = read_workbook_sheet_previews(workbook_path, ["WideTall"], max_rows=2, max_cols=2)["WideTall"]

    # Then: the visible table is capped and both truncation flags are set
    assert preview.headers == ["h1", "h2"]
    assert preview.rows == [["r1c1", "r1c2"], ["r2c1", "r2c2"]]
    assert preview.truncated_rows is True
    assert preview.truncated_columns is True


def test_read_workbook_sheet_previews_handles_empty_and_single_cell_sheets(tmp_path: Path) -> None:
    """Empty and header-only sheets produce stable preview shapes."""

    # Given: a workbook with one empty sheet and one single-cell sheet
    workbook_path = tmp_path / "small.xlsx"
    workbook_path.write_bytes(create_xlsx_content({"Empty": [], "Single": [["only header"]]}))
    assert workbook_path.exists()

    # When: previews are read
    previews = read_workbook_sheet_previews(workbook_path, ["Empty", "Single"])

    # Then: empty and single-cell sheets keep distinct preview payloads
    assert previews["Empty"].headers == []
    assert previews["Empty"].rows == []
    assert previews["Empty"].truncated_rows is False
    assert previews["Empty"].truncated_columns is False
    assert previews["Single"].headers == ["only header"]
    assert previews["Single"].rows == []


async def test_stage1_upload_response_includes_xlsx_sheet_previews(app_client: AsyncClient) -> None:
    """XLSX uploads return capped previews next to sheet names."""

    # Given: an XLSX workbook ready for upload
    content = create_xlsx_content(
        {
            "Keep": [["status"], ["unchanged"]],
            "Patients": [["col_a", "col_b"], ["alpha", "value one"]],
        }
    )
    assert "Patients" not in content.decode("latin1", errors="ignore")

    # When: the workbook is uploaded
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("workbook.xlsx", content, TEST_XLSX_CONTENT_TYPE)},
    )

    # Then: the response includes a preview for each worksheet
    assert response.status_code == 201
    data = response.json()
    assert data["sheet_names"] == ["Keep", "Patients"]
    assert data["sheet_previews"]["Patients"] == {
        "headers": ["col_a", "col_b"],
        "rows": [["alpha", "value one"]],
        "truncated_rows": False,
        "truncated_columns": False,
    }


async def test_stage1_upload_response_omits_previews_for_csv(app_client: AsyncClient) -> None:
    """CSV uploads keep the same response shape with no sheet previews."""

    # Given: a CSV file ready for upload
    content = create_csv_content([["col_a"], ["alpha"]])
    assert b"PK" not in content

    # When: the CSV is uploaded
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("data.csv", content, TEST_CSV_CONTENT_TYPE)},
    )

    # Then: no workbook previews are returned
    assert response.status_code == 201
    data = response.json()
    assert data["sheet_names"] == []
    assert data["sheet_previews"] == {}
