"""Feature tests for PV (Permissible Value) integration across stages."""

from __future__ import annotations

from typing import Any, cast

from src.domain.column_assignment import extract_column_cde_mappings as _extract_column_cde_mappings
from src.domain.manifest import ManifestPayload


def _sdk_manifest(column_mappings: dict[str, dict[str, Any]]) -> ManifestPayload:
    """Test helper: simulate SDK manifests that may have extra keys beyond ColumnMappingEntry."""
    return cast(ManifestPayload, {"column_mappings": column_mappings})


class TestExtractColumnCDEMappings:
    """PV fetch should use AI-recommended column mappings from manifest."""

    def test_extracts_target_field_from_column_mappings(self) -> None:
        """Column->CDE mappings are correctly extracted from manifest's column_mappings."""

        # Given: A manifest with AI-recommended column mappings (extra keys simulate SDK output)
        manifest = _sdk_manifest({
            "patient_diagnosis": {"targetField": "primary_diagnosis", "confidence": 0.92},
            "drug_name": {"targetField": "therapeutic_agents", "confidence": 0.87},
        })

        # When: CDE mappings are extracted from the manifest
        result = _extract_column_cde_mappings(manifest)

        # Then: Column names map to their target CDE keys
        assert result == {
            "patient_diagnosis": "primary_diagnosis",
            "drug_name": "therapeutic_agents",
        }

    def test_skips_entries_without_target_field(self) -> None:
        """Columns without targetField are excluded from mappings."""

        # Given: A manifest where some columns lack targetField (extra keys simulate SDK output)
        manifest = _sdk_manifest({
            "mapped_col": {"targetField": "primary_diagnosis", "confidence": 0.9},
            "unmapped_col": {},
            "partial_col": {"confidence": 0.5},
        })

        # When: CDE mappings are extracted
        result = _extract_column_cde_mappings(manifest)

        # Then: Only columns with targetField are included
        assert "mapped_col" in result
        assert "unmapped_col" not in result
        assert "partial_col" not in result

    def test_handles_none_manifest(self) -> None:
        """Returns empty dict when manifest is None."""

        # Given: No manifest available
        # When: CDE mappings are extracted from None
        result = _extract_column_cde_mappings(None)

        # Then: Returns empty dict without error
        assert result == {}

    def test_handles_empty_column_mappings(self) -> None:
        """Returns empty dict when column_mappings is empty."""

        # Given: A manifest with empty column_mappings
        manifest: ManifestPayload = {"column_mappings": {}}

        # When: CDE mappings are extracted
        result = _extract_column_cde_mappings(manifest)

        # Then: Returns empty dict
        assert result == {}

    def test_handles_missing_column_mappings_key(self) -> None:
        """Returns empty dict when column_mappings key is missing."""

        # Given: A manifest without column_mappings key
        manifest = cast(ManifestPayload, {})

        # When: CDE mappings are extracted
        result = _extract_column_cde_mappings(manifest)

        # Then: Returns empty dict without error
        assert result == {}

    def test_deduplicates_cde_keys(self) -> None:
        """Multiple columns can map to the same CDE (dedupe happens at fetch time)."""

        # Given: Multiple columns mapping to the same CDE
        manifest = _sdk_manifest({
            "diagnosis_1": {"targetField": "primary_diagnosis", "confidence": 0.9},
            "diagnosis_2": {"targetField": "primary_diagnosis", "confidence": 0.85},
            "treatment": {"targetField": "therapeutic_agents", "confidence": 0.88},
        })

        # When: CDE mappings are extracted
        result = _extract_column_cde_mappings(manifest)

        # Then: All mappings are preserved (deduplication of CDE keys for fetch is separate)
        assert result["diagnosis_1"] == "primary_diagnosis"
        assert result["diagnosis_2"] == "primary_diagnosis"
        assert result["treatment"] == "therapeutic_agents"

        # Verify that unique CDE keys can be derived
        unique_cde_keys = list(set(result.values()))
        assert len(unique_cde_keys) == 2
        assert "primary_diagnosis" in unique_cde_keys
        assert "therapeutic_agents" in unique_cde_keys
