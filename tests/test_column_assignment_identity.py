"""Feature tests for canonical column assignment identity across review stages."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

from src.domain.cde import NO_MAPPING_SENTINEL
from src.domain.column_assignment import ColumnAssignment, build_column_assignments
from src.domain.data_model_cache import clear_all_session_caches, get_session_cache
from src.domain.manifest import ManifestPayload, ManifestRow, ManifestSummary
from src.stage_4_review_results.router import _build_columns_from_manifest
from src.stage_5_review_summary.router import _build_summary_from_manifest


def _make_row(
    column_id: int,
    column_name: str,
    original: str,
    harmonized: str,
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
        row_indices=[0],
        manual_overrides=[],
    )


class TestColumnAssignmentBuilder:
    """Resolved assignments should preserve stable column identity."""

    def test_builds_assignments_from_headers_manifest_and_overrides(self) -> None:
        """
        Given: duplicate CSV headers plus manifest mappings and manual overrides
        When: canonical assignments are built
        Then: each header position gets its own assignment and overrides win
        """
        # List positions match CSV column positions: 0=diagnosis, 1=diagnosis, 2=age
        manifest = cast(ManifestPayload, {
            "column_mappings": [
                {
                    "column_name": "diagnosis", "cde_key": "auto_dx", "cde_id": 1,
                    "harmonization": "harmonizable",
                    "alternatives": [
                        {"target": "auto_dx", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"},
                    ],
                },
                {
                    "column_name": "diagnosis", "cde_key": "auto_dx", "cde_id": 1,
                    "harmonization": "harmonizable",
                    "alternatives": [
                        {"target": "auto_dx", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"},
                    ],
                },
                {
                    "column_name": "age", "cde_key": "age_at_diagnosis", "cde_id": 2,
                    "harmonization": "harmonizable",
                    "alternatives": [
                        {
                            "target": "age_at_diagnosis", "confidence": 0.85,
                            "cde_id": 2, "harmonization": "harmonizable",
                        },
                    ],
                },
            ]
        })
        manual_overrides = {0: "manual_dx"}
        csv_headers = ["diagnosis", "diagnosis", "age"]

        assignments = build_column_assignments(manifest, manual_overrides, csv_headers)

        assert len(assignments) == 3
        # manual_dx is not in alternatives; falls through to top-level harmonization ("harmonizable")
        assert assignments[0] == ColumnAssignment(0, "diagnosis", "manual_dx", "harmonizable")
        assert assignments[1] == ColumnAssignment(1, "diagnosis", "auto_dx", "harmonizable")
        assert assignments[2] == ColumnAssignment(2, "age", "age_at_diagnosis", "harmonizable")


class TestHarmonizationResolution:
    """build_column_assignments resolves harmonization as single source of truth."""

    def test_manifest_harmonization_numeric_flows_through(self) -> None:
        """
        Given: manifest entry with harmonization="numeric" and no override
        When: assignments are built
        Then: assignments[1].harmonization == "numeric" (co-null invariant: cde_key is also non-None)

        Negative baseline: the assignment must carry a non-None cde_key (confirms this is not
        a no-mapping case where harmonization being None would be correct).
        """
        manifest = cast(ManifestPayload, {
            "column_mappings": [
                {
                    "column_name": "age", "cde_key": "age_at_dx", "cde_id": 2,
                    "harmonization": "numeric",
                    "alternatives": [
                        {"target": "age_at_dx", "confidence": 0.9, "cde_id": 2, "harmonization": "numeric"},
                    ],
                },
            ]
        })

        assignments = build_column_assignments(manifest, {}, ["age"])

        # Baseline: cde_key is present, so harmonization must NOT be None
        assert assignments[0].cde_key is not None
        assert assignments[0].harmonization == "numeric"

    def test_override_to_alternative_with_numeric_harmonization(self) -> None:
        """
        Given: manifest entry with harmonization="harmonizable" and an alternative
               with harmonization="numeric", plus a manual override pointing to that alternative
        When: assignments are built
        Then: assignments[0].harmonization == "numeric" (alternative lookup wins over top-level)
        """
        manifest = cast(ManifestPayload, {
            "column_mappings": [
                {
                    "column_name": "diagnosis", "cde_key": "dx_string", "cde_id": 1,
                    "harmonization": "harmonizable",
                    "alternatives": [
                        {"target": "dx_string", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"},
                        {"target": "dx_numeric", "confidence": 0.7, "cde_id": 3, "harmonization": "numeric"},
                    ],
                },
            ]
        })
        manual_overrides = {0: "dx_numeric"}

        assignments = build_column_assignments(manifest, manual_overrides, ["diagnosis"])

        # Override to an alternative that carries "numeric" → resolved from alternatives list
        assert assignments[0].cde_key == "dx_numeric"
        assert assignments[0].harmonization == "numeric"

    def test_no_mapping_sentinel_produces_null_cde_and_harmonization(self) -> None:
        """
        Given: manual override set to NO_MAPPING_SENTINEL
        When: assignments are built
        Then: cde_key is None AND harmonization is None (co-null invariant satisfied)
        """
        manifest = cast(ManifestPayload, {
            "column_mappings": [
                {
                    "column_name": "diagnosis", "cde_key": "dx_auto", "cde_id": 1,
                    "harmonization": "harmonizable",
                    "alternatives": [],
                },
            ]
        })
        manual_overrides = {0: NO_MAPPING_SENTINEL}

        assignments = build_column_assignments(manifest, manual_overrides, ["diagnosis"])

        assert assignments[0].cde_key is None
        assert assignments[0].harmonization is None

    def test_override_to_harmonizable_cde_from_another_column(self) -> None:
        """
        Given: column 0 is AI-mapped to a no-PV CDE (pass-through); column 1 is AI-mapped
               to a harmonizable CDE. User overrides column 0 to the harmonizable CDE
               that only appears as column 1's top-level (not in column 0's alternatives).
        When: assignments are built
        Then: assignments[0].harmonization == "harmonizable" (from the cross-column lookup,
               NOT inherited from column 0's original no-PV harmonization).

        Regression: previously, _resolve_harmonization fell through to the current column's
        entry.harmonization even when the override pointed to a CDE outside that column's
        alternatives — incorrectly routing the override to the pass_through bucket.
        """
        manifest = cast(ManifestPayload, {
            "column_mappings": [
                {
                    "column_name": "record_id", "cde_key": "participant_id", "cde_id": 342,
                    "harmonization": "no_permissible_values",
                    "alternatives": [
                        {
                            "target": "participant_id", "confidence": 0.8,
                            "cde_id": 342, "harmonization": "no_permissible_values",
                        },
                        {
                            "target": "sample_id", "confidence": 0.6,
                            "cde_id": 304, "harmonization": "no_permissible_values",
                        },
                    ],
                },
                {
                    "column_name": "morphology", "cde_key": "morphology", "cde_id": 312,
                    "harmonization": "harmonizable",
                    "alternatives": [
                        {"target": "morphology", "confidence": 1.0, "cde_id": 312, "harmonization": "harmonizable"},
                    ],
                },
            ]
        })
        manual_overrides = {0: "morphology"}

        assignments = build_column_assignments(manifest, manual_overrides, ["record_id", "morphology"])

        assert assignments[0].cde_key == "morphology"
        assert assignments[0].harmonization == "harmonizable", (
            "Override to a harmonizable CDE must not inherit the original column's no-PV harmonization"
        )

    def test_manifest_absent_column_produces_null_cde_and_harmonization(self) -> None:
        """
        Given: manifest=None (no manifest loaded for session)
        When: assignments are built
        Then: every column has cde_key=None AND harmonization=None

        Negative baseline: with no manifest the cde_key cannot be non-None, so
        harmonization must also be None (co-null invariant).
        """
        assignments = build_column_assignments(None, {}, ["diagnosis", "age"])

        for assignment in assignments.values():
            assert assignment.cde_key is None
            assert assignment.harmonization is None


class TestStage4ColumnIdentity:
    """Stage 4 should treat duplicate-named columns as distinct review units."""

    def test_duplicate_header_columns_render_as_distinct_columns(self) -> None:
        """
        Given: two manifest columns sharing the same header text
        When: Stage 4 builds column review data
        Then: both columns are preserved as separate entries ordered by column_id
        """
        clear_all_session_caches()
        file_id = "abc12345"
        rows = [
            _make_row(0, "diagnosis", "Foo", "Foo"),
            _make_row(1, "diagnosis", "Bar", "Bar"),
        ]
        manifest = ManifestSummary(
            total_terms=2,
            changed_terms=0,
            high_confidence_count=2,
            medium_confidence_count=0,
            low_confidence_count=0,
            rows=rows,
        )

        columns = _build_columns_from_manifest(manifest, file_id)

        assert len(columns) == 2
        assert [column.sourceColumnIndex for column in columns] == [0, 1]
        assert [column.columnKey for column in columns] == ["0", "1"]
        assert [column.transformations[0].originalValue for column in columns] == ["Foo", "Bar"]


class TestStage5ColumnIdentity:
    """Stage 5 summary should deduplicate mappings by stable column_id."""

    def test_duplicate_header_columns_remain_distinct_in_summary(self) -> None:
        """
        Given: two duplicate-named columns with the same original and final values
        When: Stage 5 builds the summary
        Then: both columns remain distinct term mappings because column_id differs
        """
        clear_all_session_caches()
        file_id = "def67890"
        cache = get_session_cache(file_id)
        cache.set_column_assignments({
            0: ColumnAssignment(0, "diagnosis", "dx_a", "harmonizable"),
            1: ColumnAssignment(1, "diagnosis", "dx_b", "harmonizable"),
        })
        cache.set_pvs("dx_a", frozenset(["Allowed A"]))
        cache.set_pvs("dx_b", frozenset(["Allowed B"]))
        rows = [
            _make_row(0, "diagnosis", "Shared", "Shared"),
            _make_row(1, "diagnosis", "Shared", "Shared"),
        ]
        manifest = ManifestSummary(
            total_terms=2,
            changed_terms=0,
            high_confidence_count=2,
            medium_confidence_count=0,
            low_confidence_count=0,
            rows=rows,
        )

        with patch("src.stage_5_review_summary.router.ensure_pvs_loaded", return_value=cache):
            with patch("src.stage_5_review_summary.router.get_upload_storage") as mock_get_upload_storage:
                mock_storage = MagicMock()
                mock_storage.load.return_value = None
                mock_get_upload_storage.return_value = mock_storage

                summary = _build_summary_from_manifest(manifest, file_id)

        assert len(summary.term_mappings) == 2
        assert summary.non_conformant_count == 2
