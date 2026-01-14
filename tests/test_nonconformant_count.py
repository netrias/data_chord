"""Tests for non-conformant value counting consistency between Stage 4 and Stage 5.

The gating dialog (Stage 4) and summary page (Stage 5) must report the same count
of non-conformant values. This test exercises the specific failure case where
an unchanged row has a non-conformant value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.manifest import ManifestRow, ManualOverride, get_latest_override_value
from src.domain.pv_validation import check_value_conformance


@dataclass
class MockSessionCache:
    """Mock session cache with configurable PV sets per column."""

    pvs_by_column: dict[str, frozenset[str]]

    def get_pvs_for_column(self, column_name: str) -> frozenset[str] | None:
        return self.pvs_by_column.get(column_name)


def _count_non_conformant_stage4(rows: list[ManifestRow], cache: MockSessionCache) -> int:
    """Stage 4's logic: deduplicate by (column, original, final)."""
    seen: set[tuple[str, str, str]] = set()
    count = 0

    for row in rows:
        # Get the current value (latest override > AI harmonization)
        latest_override = get_latest_override_value(row.manual_overrides)
        current_value = latest_override if latest_override else row.top_harmonization

        # Skip if we've already processed this exact mapping
        key = (row.column_name, row.to_harmonize, current_value or "")
        if key in seen:
            continue
        seen.add(key)

        # Check PV conformance using shared function for consistency with router
        pv_set = cache.get_pvs_for_column(row.column_name)
        if pv_set and current_value and not check_value_conformance(current_value, pv_set):
            count += 1

    return count


def _count_non_conformant_stage5(rows: list[ManifestRow], cache: MockSessionCache) -> int:
    """Stage 5's logic: track all rows for conformance checking."""
    unique_mappings: dict[tuple[str, str, str], bool] = {}

    for row in rows:
        final = get_latest_override_value(row.manual_overrides) or row.top_harmonization
        key = (row.column_name, row.to_harmonize, final)
        if key in unique_mappings:
            continue
        pv_set = cache.get_pvs_for_column(row.column_name)
        is_conformant = check_value_conformance(final, pv_set)
        unique_mappings[key] = is_conformant

    return sum(1 for is_conformant in unique_mappings.values() if not is_conformant)


def _make_row(
    column_name: str,
    original: str,
    harmonized: str,
    overrides: list[dict[str, Any]] | None = None,
) -> ManifestRow:
    """Helper to create a ManifestRow for testing."""
    manual_overrides = [
        ManualOverride(
            user_id=o.get("user_id", "test"),
            timestamp=o.get("timestamp", "2024-01-01T00:00:00Z"),
            value=o["value"],
        )
        for o in (overrides or [])
    ]
    return ManifestRow(
        job_id="test-job",
        column_id=0,
        column_name=column_name,
        to_harmonize=original,
        top_harmonization=harmonized,
        ontology_id=None,
        top_harmonizations=[harmonized] if harmonized else [],
        confidence_score=0.9,
        error=None,
        row_indices=[0],
        manual_overrides=manual_overrides,
        pv_adjustment=None,
    )


class TestNonConformantCountConsistency:
    """Stage 4 and Stage 5 must report the same non-conformant count."""

    def test_unchanged_non_conformant_row_is_counted(self) -> None:
        """FAILURE CASE: An unchanged row with non-conformant value must be counted.

        Before the fix, Stage 5 only tracked changed rows, so unchanged non-conformant
        values were not counted. This caused Stage 4 to show 25 but Stage 5 to show 24.
        """
        # Given: A row where original == harmonized (unchanged) but both are non-conformant
        pv_set = frozenset(["Adenocarcinoma", "Squamous Cell Carcinoma"])
        cache = MockSessionCache(pvs_by_column={"primary_diagnosis": pv_set})

        # "Bad Value" is not in the PV set, and AI didn't change it
        rows = [_make_row("primary_diagnosis", "Bad Value", "Bad Value")]

        # When: Both stages count non-conformant values
        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        # Then: Both stages report the same count
        assert stage4_count == 1, "Stage 4 should count unchanged non-conformant row"
        assert stage5_count == 1, "Stage 5 should count unchanged non-conformant row"
        assert stage4_count == stage5_count

    def test_changed_non_conformant_row_is_counted(self) -> None:
        """A row changed by AI to a non-conformant value is counted by both stages."""
        pv_set = frozenset(["Adenocarcinoma", "Squamous Cell Carcinoma"])
        cache = MockSessionCache(pvs_by_column={"primary_diagnosis": pv_set})

        # AI changed "Original" to "Bad Harmonization" which is not in PV set
        rows = [_make_row("primary_diagnosis", "Original", "Bad Harmonization")]

        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        assert stage4_count == 1
        assert stage5_count == 1
        assert stage4_count == stage5_count

    def test_manual_override_to_non_conformant_is_counted(self) -> None:
        """A manual override to a non-conformant value is counted by both stages."""
        pv_set = frozenset(["Adenocarcinoma", "Squamous Cell Carcinoma"])
        cache = MockSessionCache(pvs_by_column={"primary_diagnosis": pv_set})

        # AI harmonized to conformant, but user overrode to non-conformant
        rows = [
            _make_row(
                "primary_diagnosis",
                "Original",
                "Adenocarcinoma",  # AI chose conformant
                overrides=[{"value": "User Non-Conformant Value"}],  # User overrode to non-conformant
            )
        ]

        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        assert stage4_count == 1
        assert stage5_count == 1
        assert stage4_count == stage5_count

    def test_conformant_values_not_counted(self) -> None:
        """Values that match the PV set are not counted as non-conformant."""
        pv_set = frozenset(["Adenocarcinoma", "Squamous Cell Carcinoma"])
        cache = MockSessionCache(pvs_by_column={"primary_diagnosis": pv_set})

        rows = [
            _make_row("primary_diagnosis", "Original", "Adenocarcinoma"),  # Conformant
            _make_row("primary_diagnosis", "Squamous", "Squamous Cell Carcinoma"),  # Conformant
        ]

        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        assert stage4_count == 0
        assert stage5_count == 0

    def test_deduplication_by_column_original_final(self) -> None:
        """Duplicate mappings (same column, original, final) are counted once."""
        pv_set = frozenset(["Adenocarcinoma"])
        cache = MockSessionCache(pvs_by_column={"primary_diagnosis": pv_set})

        # Same mapping appears in multiple rows (e.g., same value in multiple spreadsheet rows)
        rows = [
            _make_row("primary_diagnosis", "Bad Value", "Bad Value"),
            _make_row("primary_diagnosis", "Bad Value", "Bad Value"),
            _make_row("primary_diagnosis", "Bad Value", "Bad Value"),
        ]

        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        # Should only count once despite appearing 3 times
        assert stage4_count == 1
        assert stage5_count == 1

    def test_multiple_columns_counted_correctly(self) -> None:
        """Non-conformant values across multiple columns are all counted."""
        cache = MockSessionCache(
            pvs_by_column={
                "primary_diagnosis": frozenset(["Adenocarcinoma"]),
                "tissue_type": frozenset(["Frozen", "FFPE"]),
            }
        )

        rows = [
            _make_row("primary_diagnosis", "Bad Diagnosis", "Bad Diagnosis"),
            _make_row("tissue_type", "Bad Tissue", "Bad Tissue"),
        ]

        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        assert stage4_count == 2
        assert stage5_count == 2

    def test_column_without_pvs_not_counted(self) -> None:
        """Columns without a PV set are not counted as non-conformant."""
        # Only primary_diagnosis has PVs; other_column does not
        cache = MockSessionCache(
            pvs_by_column={
                "primary_diagnosis": frozenset(["Adenocarcinoma"]),
                # "other_column" intentionally missing - no PV set
            }
        )

        rows = [
            _make_row("primary_diagnosis", "Bad", "Bad"),  # Has PVs, non-conformant
            _make_row("other_column", "Anything", "Anything"),  # No PVs, not counted
        ]

        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        assert stage4_count == 1
        assert stage5_count == 1

    def test_empty_value_not_counted(self) -> None:
        """Empty/None values are not counted as non-conformant (graceful degradation)."""
        pv_set = frozenset(["Adenocarcinoma"])
        cache = MockSessionCache(pvs_by_column={"primary_diagnosis": pv_set})

        rows = [
            _make_row("primary_diagnosis", "", ""),  # Empty original and harmonized
            _make_row("primary_diagnosis", "Something", ""),  # Empty harmonized
        ]

        stage4_count = _count_non_conformant_stage4(rows, cache)
        stage5_count = _count_non_conformant_stage5(rows, cache)

        assert stage4_count == 0
        assert stage5_count == 0
