"""Stage 3 boundary proof for shared column outcomes and PV status."""

from __future__ import annotations

from typing import cast

from src.domain.harmonization import MatchFidelity
from src.domain.manifest import ColumnMappingManifest, ManifestPayload, ManifestRow, ManifestSummary
from src.persistence.pv_manifest_store import ColumnPvSets
from src.stage_3_harmonize.result_summary import build_harmonization_manifest_summary


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
        match_fidelity=MatchFidelity.STRONG,
        error=None,
        row_indices=row_indices if row_indices is not None else [0],
        manual_overrides=[],
    )


class TestSummaryAggregation:
    """The Stage 3 result summary preserves column outcome behavior."""

    def test_aggregates_across_columns(self) -> None:
        # Given: two columns contain five values outside their approved lists.
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
            rows=rows,
        )
        column_pv_map = ColumnPvSets({
            rows[0].column_key: frozenset(["Good"]),
            rows[3].column_key: frozenset(["Good"]),
        })

        # When: Stage 3 builds the result summary.
        schema = build_harmonization_manifest_summary(manifest, column_pv_map)

        # Then: all five values are reported as non-conformant.
        assert schema.non_conformant_terms == 5

    def test_columns_without_pvs_contribute_zero(self) -> None:
        # Given: one column has an approved list and one does not.
        rows = [
            _make_row("with_pvs", "Bad", "Bad"),
            _make_row("no_pvs", "Anything", "Anything", column_id=1),
        ]
        manifest = ManifestSummary(
            total_terms=2,
            changed_terms=0,
            rows=rows,
        )
        column_pv_map = ColumnPvSets({
            rows[0].column_key: frozenset(["Good"]),
            rows[1].column_key: None,
        })

        # When: Stage 3 builds the result summary.
        schema = build_harmonization_manifest_summary(manifest, column_pv_map)

        # Then: only the column with an approved list contributes to the count.
        assert schema.non_conformant_terms == 1

    def test_blank_provider_result_is_source_pass_through_and_columns_keep_source_order(self) -> None:
        # Given: provider output contains a blank pass-through value and columns are out of source order.
        rows = [
            _make_row("later", "Changed", "changed", row_indices=[0, 1], column_id=2),
            _make_row("first", "Something", "", column_id=0),
        ]
        manifest = ManifestSummary(
            total_terms=2,
            changed_terms=1,
            rows=rows,
        )
        column_pv_map = ColumnPvSets({
            rows[0].column_key: frozenset(["changed"]),
            rows[1].column_key: frozenset(["Allowed"]),
        })

        # When: Stage 3 builds the result summary.
        schema = build_harmonization_manifest_summary(manifest, column_pv_map)

        # Then: the blank value remains unchanged and column results follow source order.
        assert [column.source_column_index for column in schema.column_breakdowns] == [0, 2]
        first, later = schema.column_breakdowns
        assert first.changed_rows == 0
        assert first.non_conformant_terms == 1
        assert later.unique_terms_changed == 1
        assert later.changed_rows == 2


class TestMappingExtraction:
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
        result = {
            str(column_key): cde_key
            for column_key, cde_key in ColumnMappingManifest.from_payload(manifest).column_cde_map().mappings.items()
        }

        # Then
        assert "good" in result
        assert "bad" not in result
