"""Requirement tests for stage dependency boundaries."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.requirements("R-061")
def test_r061__stage_modules_do_not_import_other_stage_modules() -> None:
    """
    Given: The source tree contains stage-specific Python modules.
    When: The cross-stage import checker runs.
    Then: No stage module imports directly from another stage module.
    """
    # Given
    checker_path = Path("scripts/check-cross-stage-imports.sh")
    assert checker_path.exists()

    # When
    result = subprocess.run(["bash", str(checker_path)], check=False, capture_output=True, text=True)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
