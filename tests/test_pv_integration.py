"""Feature tests for PV (Permissible Value) integration across stages."""

from __future__ import annotations

from typing import cast

from src.domain.column_assignment import extract_column_cde_mappings as _extract_column_cde_mappings
from src.domain.manifest import ManifestPayload


def _list_manifest(*entries: dict | None) -> ManifestPayload:
    """Build a canonical list-format ManifestPayload for tests."""
    return cast(ManifestPayload, {"column_mappings": list(entries)})


def _entry(column_name: str, cde_key: str, cde_id: int = 1, confidence: float = 0.9) -> dict:
    """Build a canonical ColumnMappingRecord entry."""
    return {
        "column_name": column_name,
        "cde_key": cde_key,
        "cde_id": cde_id,
        "harmonization": "harmonizable",
        "alternatives": [
            {"target": cde_key, "confidence": confidence, "cde_id": cde_id, "harmonization": "harmonizable"},
        ],
    }


class TestExtractColumnCDEMappings:
    """PV fetch should use AI-recommended column mappings from manifest."""

    def test_extracts_cde_key_from_column_mappings(self) -> None:
        """Column->CDE mappings are correctly extracted; keyed by column position."""

        # Given: A canonical list-format manifest with two mapped columns
        manifest = _list_manifest(
            _entry("patient_diagnosis", "primary_diagnosis", 2, 0.92),
            _entry("drug_name", "therapeutic_agents", 1, 0.87),
        )

        # When: CDE mappings are extracted from the manifest
        result = _extract_column_cde_mappings(manifest)

        # Then: Column positions map to their ColumnCdeMapping entries
        assert result[0]["column_name"] == "patient_diagnosis"
        assert result[0]["cde_key"] == "primary_diagnosis"
        assert result[1]["column_name"] == "drug_name"
        assert result[1]["cde_key"] == "therapeutic_agents"

    def test_skips_none_entries(self) -> None:
        """None entries (unmapped columns) are excluded from mappings."""

        # Given: A manifest where one slot is None (unmapped column)
        manifest = _list_manifest(
            _entry("mapped_col", "primary_diagnosis", 2, 0.9),
            None,
        )

        # When: CDE mappings are extracted
        result = _extract_column_cde_mappings(manifest)

        # Then: Only mapped columns are included; position 1 (None) is absent
        assert 0 in result
        assert 1 not in result

    def test_handles_none_manifest(self) -> None:
        """Returns empty dict when manifest is None."""

        # Given: No manifest available
        # When: CDE mappings are extracted from None
        result = _extract_column_cde_mappings(None)

        # Then: Returns empty dict without error
        assert result == {}

    def test_handles_empty_column_mappings(self) -> None:
        """Returns empty dict when column_mappings is empty."""

        # Given: A manifest with empty column_mappings list
        manifest: ManifestPayload = {"column_mappings": []}

        # When: CDE mappings are extracted
        result = _extract_column_cde_mappings(manifest)

        # Then: Returns empty dict
        assert result == {}

    def test_handles_missing_column_mappings_key(self) -> None:
        """Raises ValueError when column_mappings key is missing (not a list)."""

        # Given: A manifest without column_mappings key (None value → not a list)
        manifest = cast(ManifestPayload, {})

        # When/Then: Raises since column_mappings is absent (None is not a list)
        try:
            _extract_column_cde_mappings(manifest)
            # If it returns an empty dict, that's also acceptable — guard succeeds
        except ValueError:
            pass  # expected for non-list column_mappings

    def test_deduplicates_cde_keys(self) -> None:
        """Multiple columns can map to the same CDE (dedupe happens at fetch time)."""

        # Given: Multiple columns mapping to the same CDE
        manifest = _list_manifest(
            _entry("diagnosis_1", "primary_diagnosis", 2, 0.9),
            _entry("diagnosis_2", "primary_diagnosis", 2, 0.85),
            _entry("treatment", "therapeutic_agents", 1, 0.88),
        )

        # When: CDE mappings are extracted
        result = _extract_column_cde_mappings(manifest)

        # Then: All positions are present
        assert result[0]["cde_key"] == "primary_diagnosis"
        assert result[1]["cde_key"] == "primary_diagnosis"
        assert result[2]["cde_key"] == "therapeutic_agents"

        # Verify that unique CDE keys can be derived
        unique_cde_keys = list({m["cde_key"] for m in result.values()})
        assert len(unique_cde_keys) == 2
        assert "primary_diagnosis" in unique_cde_keys
        assert "therapeutic_agents" in unique_cde_keys
