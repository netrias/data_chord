"""Stage 3 boundary proof for shared column outcomes and PV status."""

from __future__ import annotations

from typing import cast

from src.domain.manifest import ColumnMappingManifest, ManifestPayload, ManifestRow, ManifestSummary
from src.persistence.pv_manifest_store import ColumnPvSets
from src.stage_3_harmonize.router import _convert_to_schema


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

    def test_blank_provider_result_is_source_pass_through_and_columns_keep_source_order(self) -> None:
        rows = [
            _make_row("later", "Changed", "changed", row_indices=[0, 1], column_id=2),
            _make_row("first", "Something", "", column_id=0),
        ]
        manifest = ManifestSummary(
            total_terms=2,
            changed_terms=1,
            high_confidence_count=2,
            medium_confidence_count=0,
            low_confidence_count=0,
            rows=rows,
        )
        column_pv_map = ColumnPvSets({
            rows[0].column_key: frozenset(["changed"]),
            rows[1].column_key: frozenset(["Allowed"]),
        })

        schema = _convert_to_schema(manifest, column_pv_map)

        assert [column.source_column_index for column in schema.column_breakdowns] == [0, 2]
        first, later = schema.column_breakdowns
        assert first.changed_rows == 0
        assert first.non_conformant_terms == 1
        assert later.unique_terms_changed == 1
        assert later.changed_rows == 2


class TestManualOverridePropagation:
    """Manual overrides must merge into column-CDE mappings for PV lookup."""

    def test_harmonize_request_accepts_null_manual_override(self) -> None:
        """
        Given: Stage 2 sends null for an explicit No Mapping choice
        When: the Stage 3 request model validates the payload
        Then: the null is preserved for the domain normalizer to remove the
              manifest mapping
        """
        from src.api.schemas import HarmonizeRequest

        # Given
        payload = {
            "file_id": "abcdef0123456789abcdef0123456789",
            "data_model_key": "CCDI",
            "external_version_number": "11.0.4",
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
        from src.api.schemas import HarmonizeRequest

        # Given
        payload = {
            "file_id": "abcdef0123456789abcdef0123456789",
            "data_model_key": "CCDI",
            "external_version_number": "11.0.4",
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
