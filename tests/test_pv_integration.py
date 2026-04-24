"""Feature tests for PV (Permissible Value) integration across stages."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest

from src.domain.column_assignment import ColumnAssignment, build_column_assignments
from src.domain.data_model_cache import SessionCache
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


class TestBuildColumnAssignmentsFromManifest:
    """PV fetch should use canonical assignments resolved from the manifest."""

    def test_builds_assignments_with_cde_keys_from_column_mappings(self) -> None:
        """Column->CDE mappings are resolved into assignments keyed by column position."""

        # Given: A canonical list-format manifest with two mapped columns
        manifest = _list_manifest(
            _entry("patient_diagnosis", "primary_diagnosis", 2, 0.92),
            _entry("drug_name", "therapeutic_agents", 1, 0.87),
        )

        # When: CDE mappings are resolved from the manifest
        result = build_column_assignments(manifest, {}, ["patient_diagnosis", "drug_name"])

        # Then: Column positions map to canonical assignments
        assert result[0].column_name == "patient_diagnosis"
        assert result[0].cde_key == "primary_diagnosis"
        assert result[1].column_name == "drug_name"
        assert result[1].cde_key == "therapeutic_agents"

    def test_none_entries_become_unmapped_assignments(self) -> None:
        """None entries keep their column identity with no assigned CDE."""

        # Given: A manifest where one slot is None (unmapped column)
        manifest = _list_manifest(
            _entry("mapped_col", "primary_diagnosis", 2, 0.9),
            None,
        )

        # When: assignments are resolved
        result = build_column_assignments(manifest, {}, ["mapped_col", "unmapped_col"])

        # Then: Both positions remain present; position 1 has no CDE
        assert result[0].cde_key == "primary_diagnosis"
        assert result[1].column_name == "unmapped_col"
        assert result[1].cde_key is None

    def test_handles_none_manifest(self) -> None:
        """Returns unmapped assignments when manifest is None."""

        # Given: No manifest available
        # When: assignments are resolved from None
        result = build_column_assignments(None, {}, ["diagnosis"])

        # Then: Column identity is preserved without a CDE
        assert result[0] == ColumnAssignment(0, "diagnosis", None, None)

    def test_handles_empty_column_mappings(self) -> None:
        """Returns unmapped assignments when column_mappings is empty."""

        # Given: A manifest with empty column_mappings list
        manifest: ManifestPayload = {"column_mappings": []}

        # When: assignments are resolved
        result = build_column_assignments(manifest, {}, ["diagnosis"])

        # Then: Column identity is preserved without a CDE
        assert result[0] == ColumnAssignment(0, "diagnosis", None, None)

    def test_handles_missing_column_mappings_key(self) -> None:
        """Treats missing column_mappings as no manifest entries."""

        # Given: A manifest without column_mappings key
        manifest = cast(ManifestPayload, {})

        # When: assignments are resolved
        result = build_column_assignments(manifest, {}, ["diagnosis"])

        # Then: The column remains present but unmapped
        assert result[0] == ColumnAssignment(0, "diagnosis", None, None)

    def test_deduplicates_cde_keys(self) -> None:
        """Multiple columns can map to the same CDE (dedupe happens at fetch time)."""

        # Given: Multiple columns mapping to the same CDE
        manifest = _list_manifest(
            _entry("diagnosis_1", "primary_diagnosis", 2, 0.9),
            _entry("diagnosis_2", "primary_diagnosis", 2, 0.85),
            _entry("treatment", "therapeutic_agents", 1, 0.88),
        )

        # When: assignments are resolved
        result = build_column_assignments(manifest, {}, ["diagnosis_1", "diagnosis_2", "treatment"])

        # Then: All positions are present
        assert result[0].cde_key == "primary_diagnosis"
        assert result[1].cde_key == "primary_diagnosis"
        assert result[2].cde_key == "therapeutic_agents"

        # Verify that unique CDE keys can be derived
        unique_cde_keys = list({m.cde_key for m in result.values() if m.cde_key is not None})
        assert len(unique_cde_keys) == 2
        assert "primary_diagnosis" in unique_cde_keys
        assert "therapeutic_agents" in unique_cde_keys


def _make_cache_with_model_info() -> SessionCache:
    """Session cache seeded with model info so _validate_pv_fetch_preconditions returns."""
    cache = SessionCache()
    from src.domain.cde import CDEInfo
    cache.set_cdes(
        [CDEInfo(cde_id=1, cde_key="dx_cde", description=None, version_label="v1")],
        data_model_key="CCDI",
        version_label="v1",
    )
    return cache


class TestFetchPVsForSessionFiltering:
    """_fetch_pvs_for_session must only fetch cde_keys for harmonizable assignments."""

    @pytest.mark.asyncio
    async def test_fetches_only_harmonizable_cde_key(self) -> None:
        """Given one harmonizable + one numeric assignment, only the harmonizable cde_key is fetched."""
        # Given: numeric col is already populated in assignments (not filtered pre-call)
        assignments: dict[int, ColumnAssignment] = {
            0: ColumnAssignment(0, "dx", "dx_cde", "harmonizable"),
            1: ColumnAssignment(1, "age", "age_cde", "numeric"),
        }
        cache = _make_cache_with_model_info()
        fetched_keys: list[list[str]] = []

        async def _capture_fetch(
            _cache: object, data_model_key: str, version_label: str, cde_keys: list[str], file_id: str
        ) -> None:
            fetched_keys.append(cde_keys)

        with (
            patch("src.stage_3_harmonize.router.get_session_cache", return_value=cache),
            patch("src.stage_3_harmonize.router._fetch_and_cache_pvs", side_effect=_capture_fetch),
        ):
            from src.stage_3_harmonize.router import _fetch_pvs_for_session
            await _fetch_pvs_for_session("file-001", assignments, "CCDI")

        assert fetched_keys == [["dx_cde"]]

    @pytest.mark.asyncio
    async def test_skips_no_permissible_values_cde_key(self) -> None:
        """Given an override to a no_permissible_values CDE, that cde_key is NOT fetched."""
        # Given: user overrode to a CDE that has no PV set — assignment reflects the resolved status
        assignments: dict[int, ColumnAssignment] = {
            0: ColumnAssignment(0, "tumor", "tumor_cde", "no_permissible_values"),
        }
        cache = _make_cache_with_model_info()
        # Update cde in cache to match the assignment's cde_key
        from src.domain.cde import CDEInfo
        cache.set_cdes(
            [CDEInfo(cde_id=2, cde_key="tumor_cde", description=None, version_label="v1")],
            data_model_key="CCDI",
            version_label="v1",
        )
        fetched_keys: list[list[str]] = []

        async def _capture_fetch(
            _cache: object, data_model_key: str, version_label: str, cde_keys: list[str], file_id: str
        ) -> None:
            fetched_keys.append(cde_keys)  # pragma: no cover

        with (
            patch("src.stage_3_harmonize.router.get_session_cache", return_value=cache),
            patch("src.stage_3_harmonize.router._fetch_and_cache_pvs", side_effect=_capture_fetch),
        ):
            from src.stage_3_harmonize.router import _fetch_pvs_for_session
            await _fetch_pvs_for_session("file-002", assignments, "CCDI")

        # No fetch should be triggered — empty cde_keys causes early return
        assert fetched_keys == []

    @pytest.mark.asyncio
    async def test_override_from_numeric_to_harmonizable_is_fetched(self) -> None:
        """Given an override from numeric to harmonizable, the new cde_key IS fetched."""
        # Given: AI suggested numeric CDE; user overrode to harmonizable CDE —
        # assignment.harmonization="harmonizable" reflects the resolved override
        assignments: dict[int, ColumnAssignment] = {
            0: ColumnAssignment(0, "age", "age_harmonizable_cde", "harmonizable"),
        }
        cache = _make_cache_with_model_info()
        from src.domain.cde import CDEInfo
        cache.set_cdes(
            [CDEInfo(cde_id=3, cde_key="age_harmonizable_cde", description=None, version_label="v1")],
            data_model_key="CCDI",
            version_label="v1",
        )
        fetched_keys: list[list[str]] = []

        async def _capture_fetch(
            _cache: object, data_model_key: str, version_label: str, cde_keys: list[str], file_id: str
        ) -> None:
            fetched_keys.append(cde_keys)

        with (
            patch("src.stage_3_harmonize.router.get_session_cache", return_value=cache),
            patch("src.stage_3_harmonize.router._fetch_and_cache_pvs", side_effect=_capture_fetch),
        ):
            from src.stage_3_harmonize.router import _fetch_pvs_for_session
            await _fetch_pvs_for_session("file-003", assignments, "CCDI")

        assert fetched_keys == [["age_harmonizable_cde"]]
