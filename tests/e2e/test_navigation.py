"""End-to-end browser tests for multi-stage workflow navigation."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_upload_page_loads(server: str, page: Page) -> None:
    """Stage 1 upload page loads and shows dropzone."""
    page.goto(f"{server}/stage-1")

    dropzone = page.locator("#dropzone")
    expect(dropzone).to_be_visible()


def test_file_upload_shows_in_dropzone(
    server: str,
    page: Page,
    sample_csv_path: Path,
) -> None:
    """Uploading a file displays file info in the dropzone."""
    page.goto(f"{server}/stage-1")

    file_input = page.locator("#fileInput")
    file_input.set_input_files(str(sample_csv_path))

    file_name_display = page.locator("#dropzoneFileName")
    expect(file_name_display).to_have_text(sample_csv_path.name, timeout=10000)


def test_back_to_stage1_shows_uploaded_file(
    server: str,
    page: Page,
    sample_csv_path: Path,
) -> None:
    """Navigating back to Stage 1 shows the previously uploaded file."""
    # Given: Upload a file
    page.goto(f"{server}/stage-1")

    file_input = page.locator("#fileInput")
    file_input.set_input_files(str(sample_csv_path))

    file_name_display = page.locator("#dropzoneFileName")
    expect(file_name_display).to_have_text(sample_csv_path.name, timeout=10000)

    # When: Navigate away and back
    page.goto(f"{server}/stage-2")
    page.goto(f"{server}/stage-1")

    # Then: File should still be displayed
    expect(file_name_display).to_have_text(sample_csv_path.name)


def test_new_upload_clears_previous_session(
    server: str,
    page: Page,
    sample_csv_path: Path,
) -> None:
    """Uploading a new file clears the previous file session."""
    # Given: Upload initial file
    page.goto(f"{server}/stage-1")

    file_input = page.locator("#fileInput")
    file_input.set_input_files(str(sample_csv_path))

    file_name_display = page.locator("#dropzoneFileName")
    expect(file_name_display).to_have_text(sample_csv_path.name, timeout=10000)

    # When: Click change file and upload different file
    change_button = page.locator("#changeFileButton")
    change_button.click()

    # Re-upload same file (simulates choosing a different file)
    file_input.set_input_files(str(sample_csv_path))

    # Then: File should be displayed (fresh upload)
    expect(file_name_display).to_have_text(sample_csv_path.name, timeout=10000)


def test_session_storage_persists_file_session(
    server: str,
    page: Page,
    sample_csv_path: Path,
) -> None:
    """File session is stored in sessionStorage after upload."""
    page.goto(f"{server}/stage-1")

    file_input = page.locator("#fileInput")
    file_input.set_input_files(str(sample_csv_path))

    # Wait for upload to complete
    file_name_display = page.locator("#dropzoneFileName")
    expect(file_name_display).to_have_text(sample_csv_path.name, timeout=10000)

    # Check sessionStorage
    session_data = page.evaluate("sessionStorage.getItem('currentFileSession')")
    assert session_data is not None

    import json

    parsed = json.loads(session_data)
    assert "file_id" in parsed
    assert parsed["original_name"] == sample_csv_path.name


def test_progress_tracker_preserves_file_id_in_url(
    server: str,
    page: Page,
    sample_csv_path: Path,
) -> None:
    """Clicking progress tracker steps preserves file_id in URL."""
    import json

    # Given: Upload a file and advance to Stage 2 via analyze button
    page.goto(f"{server}/stage-1")

    file_input = page.locator("#fileInput")
    file_input.set_input_files(str(sample_csv_path))

    file_name_display = page.locator("#dropzoneFileName")
    expect(file_name_display).to_have_text(sample_csv_path.name, timeout=10000)

    # Get the file_id from session
    session_data = page.evaluate("sessionStorage.getItem('currentFileSession')")
    file_id = json.loads(session_data)["file_id"]

    # Click "Map columns" button to advance to Stage 2
    analyze_button = page.locator("#analyzeButton")
    analyze_button.click()
    page.wait_for_url("**/stage-2**", timeout=30000)

    # When: Navigate back to Stage 1 via progress tracker
    stage_1_step = page.locator(".progress-tracker .step[data-stage='upload']")
    stage_1_step.click()
    page.wait_for_url("**/stage-1**")

    # Then: Navigate forward to Stage 2 via progress tracker
    stage_2_step = page.locator(".progress-tracker .step[data-stage='mapping']")
    stage_2_step.click()
    page.wait_for_url("**/stage-2**")

    # file_id should be in URL
    assert f"file_id={file_id}" in page.url, f"Expected file_id in URL, got: {page.url}"


def test_progress_tracker_back_forward_navigation(
    server: str,
    page: Page,
    sample_csv_path: Path,
) -> None:
    """Navigating back and forward via progress tracker preserves file_id."""
    import json

    # Given: Upload file and advance to Stage 2 via analyze button
    page.goto(f"{server}/stage-1")

    file_input = page.locator("#fileInput")
    file_input.set_input_files(str(sample_csv_path))

    file_name_display = page.locator("#dropzoneFileName")
    expect(file_name_display).to_have_text(sample_csv_path.name, timeout=10000)

    session_data = page.evaluate("sessionStorage.getItem('currentFileSession')")
    file_id = json.loads(session_data)["file_id"]

    # Click analyze to advance to Stage 2
    analyze_button = page.locator("#analyzeButton")
    analyze_button.click()
    page.wait_for_url("**/stage-2**", timeout=30000)
    assert f"file_id={file_id}" in page.url

    # Navigate back to Stage 1 via progress tracker
    page.locator(".progress-tracker .step[data-stage='upload']").click()
    page.wait_for_url("**/stage-1**")

    # Navigate forward to Stage 2 again via progress tracker
    page.locator(".progress-tracker .step[data-stage='mapping']").click()
    page.wait_for_url("**/stage-2**")

    # Then: file_id should still be in URL
    assert f"file_id={file_id}" in page.url, f"Expected file_id preserved after back/forward, got: {page.url}"
