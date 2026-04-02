"""Feature tests for canonical column assignment identity across review stages."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

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
        manifest = cast(ManifestPayload, {
            "column_mappings": {
                "diagnosis": {"targetField": "auto_dx", "cde_id": 1},
                "age": {"targetField": "age_at_diagnosis", "cde_id": 2},
            }
        })
        manual_overrides = {0: "manual_dx"}
        csv_headers = ["diagnosis", "diagnosis", "age"]

        assignments = build_column_assignments(manifest, manual_overrides, csv_headers)

        assert len(assignments) == 3
        assert assignments[0] == ColumnAssignment(0, "diagnosis", "manual_dx")
        assert assignments[1] == ColumnAssignment(1, "diagnosis", "auto_dx")
        assert assignments[2] == ColumnAssignment(2, "age", "age_at_diagnosis")


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
        assert [column.columnKey for column in columns] == ["diagnosis", "diagnosis"]
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
            0: ColumnAssignment(0, "diagnosis", "dx_a"),
            1: ColumnAssignment(1, "diagnosis", "dx_b"),
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
