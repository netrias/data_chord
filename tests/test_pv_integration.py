"""Feature tests for PV (Permissible Value) integration across stages."""

from __future__ import annotations

from typing import Any, cast

from src.domain.manifest import ColumnMappingManifest, ManifestPayload


def _sdk_manifest(column_mappings: dict[str, dict[str, Any]]) -> ManifestPayload:
    """Test helper: simulate SDK manifests that may have extra keys beyond ColumnMappingEntry."""
    return cast(ManifestPayload, {"column_mappings": column_mappings})


def _column_cde_map(manifest: ManifestPayload | None) -> dict[str, str]:
    return ColumnMappingManifest.from_payload(manifest).column_cde_map().to_strings()


class TestExtractColumnCDEMappings:
    """PV fetch should use AI-recommended column mappings from manifest."""

    def test_extracts_cde_key_from_column_mappings(self) -> None:
        """Column->CDE mappings are correctly extracted from manifest's column_mappings."""

        # Given: A manifest with AI-recommended column mappings (extra keys simulate SDK output)
        manifest = _sdk_manifest({
            "patient_diagnosis": {"cde_key": "primary_diagnosis", "cde_id": 2, "confidence": 0.92},
            "drug_name": {"cde_key": "therapeutic_agents", "cde_id": 1, "confidence": 0.87},
        })

        # When: CDE mappings are extracted from the manifest
        result = _column_cde_map(manifest)

        # Then: Column names map to their target CDE keys
        assert result == {
            "patient_diagnosis": "primary_diagnosis",
            "drug_name": "therapeutic_agents",
        }

    def test_skips_entries_without_cde_key(self) -> None:
        """Columns without cde_key are excluded from mappings."""

        # Given: A manifest where some columns lack cde_key (extra keys simulate SDK output)
        manifest = _sdk_manifest({
            "mapped_col": {"cde_key": "primary_diagnosis", "cde_id": 2, "confidence": 0.9},
            "unmapped_col": {},
            "partial_col": {"confidence": 0.5},
        })

        # When: CDE mappings are extracted
        result = _column_cde_map(manifest)

        # Then: Only columns with cde_key are included
        assert "mapped_col" in result
        assert "unmapped_col" not in result
        assert "partial_col" not in result

    def test_handles_none_manifest(self) -> None:
        """Returns empty dict when manifest is None."""

        # Given: No manifest available
        # When: CDE mappings are extracted from None
        result = _column_cde_map(None)

        # Then: Returns empty dict without error
        assert result == {}

    def test_handles_empty_column_mappings(self) -> None:
        """Returns empty dict when column_mappings is empty."""

        # Given: A manifest with empty column_mappings
        manifest: ManifestPayload = {"column_mappings": {}}

        # When: CDE mappings are extracted
        result = _column_cde_map(manifest)

        # Then: Returns empty dict
        assert result == {}

    def test_handles_missing_column_mappings_key(self) -> None:
        """Returns empty dict when column_mappings key is missing."""

        # Given: A manifest without column_mappings key
        manifest = cast(ManifestPayload, {})

        # When: CDE mappings are extracted
        result = _column_cde_map(manifest)

        # Then: Returns empty dict without error
        assert result == {}

    def test_deduplicates_cde_keys(self) -> None:
        """Multiple columns can map to the same CDE (dedupe happens at fetch time)."""

        # Given: Multiple columns mapping to the same CDE
        manifest = _sdk_manifest({
            "diagnosis_1": {"cde_key": "primary_diagnosis", "cde_id": 2, "confidence": 0.9},
            "diagnosis_2": {"cde_key": "primary_diagnosis", "cde_id": 2, "confidence": 0.85},
            "treatment": {"cde_key": "therapeutic_agents", "cde_id": 1, "confidence": 0.88},
        })

        # When: CDE mappings are extracted
        result = _column_cde_map(manifest)

        # Then: All mappings are preserved (deduplication of CDE keys for fetch is separate)
        assert result["diagnosis_1"] == "primary_diagnosis"
        assert result["diagnosis_2"] == "primary_diagnosis"
        assert result["treatment"] == "therapeutic_agents"

        # Verify that unique CDE keys can be derived
        unique_cde_keys = list(set(result.values()))
        assert len(unique_cde_keys) == 2
        assert "primary_diagnosis" in unique_cde_keys
        assert "therapeutic_agents" in unique_cde_keys
