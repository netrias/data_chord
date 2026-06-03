"""Tests for Stage 3 non-conformant term counting in column breakdowns.

Verifies that _compute_column_stats correctly counts terms that don't match
the permissible value set, and that _convert_to_schema aggregates counts.
"""

from __future__ import annotations

from typing import cast

from src.domain import ColumnCdeOverrides
from src.domain.data_model_cache import SessionCache
from src.domain.manifest import ColumnMappingManifest, ManifestPayload, ManifestRow, ManifestSummary
from src.domain.pv_persistence import ColumnPvSets
from src.stage_3_harmonize.router import (
    _column_cde_map_for_session,
    _compute_column_stats,
    _convert_to_schema,
    _store_column_mappings_in_cache,
)


def _make_row(
    column_name: str,
    original: str,
    harmonized: str,
    row_indices: list[int] | None = None,
    column_id: int = 0,
) -> ManifestRow:
    return ManifestRow(
        job_id="test-job",
        column_id=column_id,
        column_name=column_name,
        to_harmonize=original,
        top_harmonization=harmonized,
        ontology_id=None,
        top_harmonizations=[harmonized] if harmonized else [],
        confidence_score=0.9,
        error=None,
        row_indices=row_indices if row_indices is not None else [0],
        manual_overrides=[],
    )


class TestComputeColumnStatsNonConformant:
    """Non-conformant counts in _compute_column_stats."""

    def test_unchanged_non_conformant_counted(self) -> None:
        """Unchanged values not in PV set must be counted."""
        pv_set = frozenset(["Adenocarcinoma", "Squamous Cell Carcinoma"])
        rows = [
            _make_row("dx", "Bad Value", "Bad Value"),
            _make_row("dx", "Another Bad", "Another Bad"),
            _make_row("dx", "Adenocarcinoma", "Adenocarcinoma"),
        ]

        # Given: without PVs, no terms are non-conformant
        stats_no_pvs = _compute_column_stats(rows, None)
        assert stats_no_pvs.non_conformant_terms == 0

        # When: PV set provided
        stats = _compute_column_stats(rows, pv_set)

        # Then: 2 non-conformant (Bad Value, Another Bad)
        assert stats.non_conformant_terms == 2

    def test_no_pvs_counts_zero(self) -> None:
        """Graceful degradation: no PV set means 0 non-conformant."""
        rows = [_make_row("dx", "Anything", "Anything")]

        stats = _compute_column_stats(rows, None)

        assert stats.non_conformant_terms == 0

    def test_all_conformant_counts_zero(self) -> None:
        """Values already in PV set should not be counted."""
        pv_set = frozenset(["Adenocarcinoma", "Squamous Cell Carcinoma"])
        rows = [
            _make_row("dx", "Original", "Adenocarcinoma"),
            _make_row("dx", "Other", "Squamous Cell Carcinoma"),
        ]

        # Given: both harmonized values are in the PV set
        assert "Adenocarcinoma" in pv_set
        assert "Squamous Cell Carcinoma" in pv_set

        stats = _compute_column_stats(rows, pv_set)

        assert stats.non_conformant_terms == 0

    def test_empty_value_not_counted(self) -> None:
        """Empty harmonized values are conformant (missing data, not invalid)."""
        pv_set = frozenset(["Adenocarcinoma"])
        rows = [
            _make_row("dx", "", ""),
            _make_row("dx", "Something", ""),
        ]

        stats = _compute_column_stats(rows, pv_set)

        assert stats.non_conformant_terms == 0

    def test_case_sensitive_conformance(self) -> None:
        """Per domain rules, case differences are semantically significant."""
        pv_set = frozenset(["Lung Cancer"])
        rows = [_make_row("dx", "Lung cancer", "Lung cancer")]

        # Given: the value differs only in case
        assert "Lung cancer" != "Lung Cancer"

        stats = _compute_column_stats(rows, pv_set)

        assert stats.non_conformant_terms == 1

    def test_changed_rows_and_non_conformant_independent(self) -> None:
        """Changed and non-conformant counts are orthogonal."""
        pv_set = frozenset(["Adenocarcinoma"])
        rows = [
            _make_row("dx", "Original", "Bad AI Suggestion"),  # changed + non-conformant
            _make_row("dx", "Bad Value", "Bad Value"),  # unchanged + non-conformant
            _make_row("dx", "Other", "Adenocarcinoma"),  # changed + conformant
        ]

        stats = _compute_column_stats(rows, pv_set)

        assert stats.unique_terms_changed == 2
        assert stats.non_conformant_terms == 2


class TestSummaryAggregation:
    """_convert_to_schema aggregates non-conformant counts across columns."""

    def test_aggregates_across_columns(self) -> None:
        rows = [
            _make_row("col_a", "Bad1", "Bad1"),
            _make_row("col_a", "Bad2", "Bad2"),
            _make_row("col_a", "Bad3", "Bad3"),
            _make_row("col_b", "BadX", "BadX", column_id=1),
            _make_row("col_b", "BadY", "BadY", column_id=1),
        ]
        manifest = ManifestSummary(
            total_terms=5,
            changed_terms=0,
            high_confidence_count=5,
            medium_confidence_count=0,
            low_confidence_count=0,
            rows=rows,
        )
        column_pv_map = ColumnPvSets({
            rows[0].column_key: frozenset(["Good"]),
            rows[3].column_key: frozenset(["Good"]),
        })

        schema = _convert_to_schema(manifest, column_pv_map)

        assert schema.non_conformant_terms == 5

    def test_columns_without_pvs_contribute_zero(self) -> None:
        rows = [
            _make_row("with_pvs", "Bad", "Bad"),
            _make_row("no_pvs", "Anything", "Anything", column_id=1),
        ]
        manifest = ManifestSummary(
            total_terms=2,
            changed_terms=0,
            high_confidence_count=2,
            medium_confidence_count=0,
            low_confidence_count=0,
            rows=rows,
        )
        column_pv_map = ColumnPvSets({
            rows[0].column_key: frozenset(["Good"]),
            rows[1].column_key: None,
        })

        schema = _convert_to_schema(manifest, column_pv_map)

        assert schema.non_conformant_terms == 1


class TestManualOverridePropagation:
    """Manual overrides must merge into column-CDE mappings for PV lookup."""

    def test_manual_overrides_merge_with_manifest_mappings(self) -> None:
        """
        Given: a manifest with column "breed" mapped to "organism_species"
               and a manual override mapping "diagnosis" to "primary_diagnosis"
        When: _store_column_mappings_in_cache is called
        Then: cache contains both the manifest mapping AND the manual override
        """
        # Given
        cache = SessionCache()
        manifest = cast(ManifestPayload, {
            "column_mappings": {
                "breed": {"cde_key": "organism_species", "cde_id": 131},
            }
        })
        manual_overrides = {"diagnosis": "primary_diagnosis"}
        assert "diagnosis" not in cache.get_column_mappings().to_strings()

        # When
        column_cde_map = _column_cde_map_for_session(
            ColumnMappingManifest.from_payload(manifest),
            ColumnCdeOverrides.from_strings(manual_overrides),
        )
        _store_column_mappings_in_cache(cache, column_cde_map)

        # Then: both mappings present
        assert cache.get_column_mappings().to_strings() == {
            "breed": "organism_species",
            "diagnosis": "primary_diagnosis",
        }

    def test_manual_override_takes_precedence_over_manifest(self) -> None:
        """
        Given: a manifest mapping "col" to "auto_target"
               and a manual override mapping "col" to "manual_target"
        When: _store_column_mappings_in_cache is called
        Then: the manual override wins
        """
        # Given
        cache = SessionCache()
        manifest = cast(ManifestPayload, {
            "column_mappings": {
                "col": {"cde_key": "auto_target", "cde_id": 1},
            }
        })
        manual_overrides = {"col": "manual_target"}

        # When
        column_cde_map = _column_cde_map_for_session(
            ColumnMappingManifest.from_payload(manifest),
            ColumnCdeOverrides.from_strings(manual_overrides),
        )
        _store_column_mappings_in_cache(cache, column_cde_map)

        # Then: manual override wins
        assert cache.get_column_mappings().to_strings() == {"col": "manual_target"}

    def test_null_manual_override_removes_manifest_mapping(self) -> None:
        """
        Given: a manifest mapping "col" to "auto_target" and the user selected
               No Mapping in Stage 2
        When: the Stage 3 column map is built from a null manual override
        Then: the manifest mapping is removed instead of treating a UI sentinel
              as a real CDE key
        """
        # Given
        cache = SessionCache()
        manifest = cast(ManifestPayload, {
            "column_mappings": {
                "col": {"cde_key": "auto_target", "cde_id": 1},
            }
        })
        manual_overrides = {"col": None}
        assert "col" not in cache.get_column_mappings().to_strings()

        # When
        column_cde_map = _column_cde_map_for_session(
            ColumnMappingManifest.from_payload(manifest),
            ColumnCdeOverrides.from_strings(manual_overrides),
        )
        _store_column_mappings_in_cache(cache, column_cde_map)

        # Then
        assert "col" not in cache.get_column_mappings().to_strings()

    def test_harmonize_request_accepts_null_manual_override(self) -> None:
        """
        Given: Stage 2 sends null for an explicit No Mapping choice
        When: the Stage 3 request model validates the payload
        Then: the null is preserved for the domain normalizer to remove the
              manifest mapping
        """
        from src.domain.schemas import HarmonizeRequest

        # Given
        payload = {
            "file_id": "abcdef0123456789abcdef0123456789",
            "target_schema": "CCDI",
            "target_external_version_number": "11.0.4",
            "manual_overrides": {"col": None},
        }
        assert payload["manual_overrides"]["col"] is None

        # When
        request = HarmonizeRequest.model_validate(payload)

        # Then
        assert request.manual_overrides == {"col": None}

    def test_harmonize_request_accepts_column_renames(self) -> None:
        """
        Given: Stage 2 sends selected output column names
        When: the Stage 3 request model validates the payload
        Then: the rename map is preserved separately from CDE overrides
        """
        from src.domain.schemas import HarmonizeRequest

        # Given
        payload = {
            "file_id": "abcdef0123456789abcdef0123456789",
            "target_schema": "CCDI",
            "target_external_version_number": "11.0.4",
            "manual_overrides": {"col_0000": "primary_diagnosis"},
            "column_renames": {"col_0000": "Primary Diagnosis"},
        }
        assert payload["column_renames"]["col_0000"] == "Primary Diagnosis"

        # When
        request = HarmonizeRequest.model_validate(payload)

        # Then
        assert request.manual_overrides == {"col_0000": "primary_diagnosis"}
        assert request.column_renames == {"col_0000": "Primary Diagnosis"}

    def test_extract_skips_entries_without_target_field(self) -> None:
        """
        Given: a manifest with one valid and one missing cde_key entry
        When: the manifest domain model extracts column-CDE mappings
        Then: only the valid entry is returned
        """
        # Given
        manifest = cast(ManifestPayload, {
            "column_mappings": {
                "good": {"cde_key": "age", "cde_id": 1},
                "bad": {"cde_id": 2},
            }
        })

        # When
        result = ColumnMappingManifest.from_payload(manifest).column_cde_map().to_strings()

        # Then
        assert "good" in result
        assert "bad" not in result
