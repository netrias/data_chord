"""Tests for AI output whitespace stripping at the reader boundary.

Validates that artifact whitespace from the harmonization service is stripped
when reading manifest parquet, while user data (to_harmonize) is preserved.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from src.domain.manifest.models import ManifestRow
from src.domain.manifest.reader import read_manifest_parquet
from src.domain.manifest.writer import _write_manifest_parquet


def _make_row(
    to_harmonize: str = "lung cancer",
    top_harmonization: str = "Lung Cancer",
    top_harmonizations: list[str] | None = None,
) -> ManifestRow:
    return ManifestRow(
        job_id="j1",
        column_id=0,
        column_name="dx",
        to_harmonize=to_harmonize,
        top_harmonization=top_harmonization,
        ontology_id=None,
        top_harmonizations=top_harmonizations or ["Lung Cancer"],
        confidence_score=0.9,
        error=None,
        row_indices=[0],
        manual_overrides=[],
    )


def _write_and_read_back(row: ManifestRow) -> ManifestRow:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "manifest.parquet"
        _write_manifest_parquet(path, [row])
        summary = read_manifest_parquet(path)
    assert summary is not None, "read_manifest_parquet returned None"
    assert len(summary.rows) == 1
    return summary.rows[0]


class TestReaderStripsAIOutput:
    """Reader strips leading/trailing whitespace from AI output fields."""

    def test_top_harmonization_is_stripped(self) -> None:
        """Artifact whitespace on top_harmonization is removed at read time."""
        # Given: manifest row with whitespace in AI output
        row = _make_row(top_harmonization="  Lung Cancer  ")
        assert row.top_harmonization == "  Lung Cancer  "  # negative: whitespace exists

        # When: written to parquet and read back
        result = _write_and_read_back(row)

        # Then: top_harmonization is stripped
        assert result.top_harmonization == "Lung Cancer"

    def test_top_harmonizations_list_items_are_stripped(self) -> None:
        """Each suggestion in the alternatives list is stripped."""
        # Given: manifest with whitespace in suggestion list
        suggestions = [" Lung Cancer ", "  Breast Cancer"]
        row = _make_row(top_harmonizations=suggestions)
        assert row.top_harmonizations[0] == " Lung Cancer "  # negative: whitespace exists

        # When: written and read back
        result = _write_and_read_back(row)

        # Then: each suggestion is stripped
        assert result.top_harmonizations == ["Lung Cancer", "Breast Cancer"]

    def test_to_harmonize_is_NOT_stripped(self) -> None:
        """User data (to_harmonize) must preserve whitespace — it is semantically significant."""
        # Given: manifest where user's original data has trailing space
        row = _make_row(to_harmonize="lung cancer ")
        assert row.to_harmonize == "lung cancer "  # negative: whitespace exists

        # When: written and read back
        result = _write_and_read_back(row)

        # Then: to_harmonize retains its trailing space
        assert result.to_harmonize == "lung cancer "
