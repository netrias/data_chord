"""Requirement tests for requirements numbering and traceability conventions."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.requirements("R-001", "R-002")
def test_r001_r002__requirements_ids_are_unique_flat_and_increasing() -> None:
    """
    Given: The project requirements document contains numbered requirements.
    When: The requirement IDs are parsed from the document.
    Then: IDs are unique, flat, and strictly increasing.
    """
    # Given
    requirements_text = Path("requirements.md").read_text(encoding="utf-8")
    ids = re.findall(r"^R-(\d{3})\.", requirements_text, flags=re.MULTILINE)
    assert ids

    # When
    numeric_ids = [int(requirement_id) for requirement_id in ids]

    # Then
    assert len(ids) == len(set(ids))
    assert numeric_ids == sorted(numeric_ids)


@pytest.mark.requirements("R-071", "R-072")
def test_r071_r072__traceability_checker_reports_requirement_tests_with_given_when_then() -> None:
    """
    Given: Requirement tests use the pytest requirements marker and Given/When/Then docstrings.
    When: The traceability checker runs.
    Then: The report links requirement IDs to tests and includes Given, When, and Then summaries.
    """
    # Given
    checker_path = Path("scripts/check_requirements_traceability.py")
    assert checker_path.exists()

    # When
    result = subprocess.run(
        [sys.executable, str(checker_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert result.returncode == 0
    assert "Tests:" in result.stdout
    assert "  - Given:" in result.stdout
    assert "  - When:" in result.stdout
    assert "  - Then:" in result.stdout
