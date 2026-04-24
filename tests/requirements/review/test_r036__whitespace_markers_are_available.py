"""Requirement tests for visible whitespace support in the review UI."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.requirements("R-036")
def test_r036__review_ui_includes_whitespace_marker_renderer_and_styles() -> None:
    """
    Given: The review UI needs to show meaningful edge whitespace differences.
    When: The Stage 4 shared review assets are inspected.
    Then: They include a whitespace marker renderer and visible marker styles.
    """
    # Given
    review_utils = Path("src/stage_4_review_results/static/shared_review_utils.js")
    review_css = Path("src/stage_4_review_results/static/stage_4_review.css")
    assert review_utils.exists()
    assert review_css.exists()

    # When
    utils_text = review_utils.read_text(encoding="utf-8")
    css_text = review_css.read_text(encoding="utf-8")

    # Then
    assert "formatWhitespaceMarkers" in utils_text
    assert "ws-marker" in utils_text
    assert ".ws-marker" in css_text
