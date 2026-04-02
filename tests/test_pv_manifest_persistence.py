"""Feature tests for PV manifest persistence and server restart recovery.

Tests the user journey: After harmonization completes, if the server restarts
(clearing in-memory cache), users can still see PV dropdowns and non-conformant
warnings when they return to Stage 4 or Stage 5.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.domain.column_assignment import ColumnAssignment
from src.domain.data_model_cache import (
    SessionCache,
    clear_all_session_caches,
    get_session_cache,
)
from src.domain.pv_persistence import ensure_pvs_loaded, load_pv_manifest_from_disk


class TestPVManifestPersistenceFeature:
    """PV manifest persistence enables recovery after server restart."""

    def test_pvs_restored_from_disk_after_cache_cleared(self) -> None:
        """FEATURE: PVs are recovered from disk manifest when cache is empty.

        Simulates: User completes Stage 3 → server restarts → user returns to Stage 4
        """
        # Given: A PV manifest was saved to disk during Stage 3
        file_id = "test_file_123"
        pv_manifest_data = {
            "data_model_key": "cptac",
            "version_label": "v2.1",
            "column_to_cde_key": {
                "0": "primary_diagnosis_cde",
                "1": "tissue_or_organ_of_origin",
            },
            "pvs": {
                "primary_diagnosis_cde": ["Adenocarcinoma", "Squamous Cell Carcinoma"],
                "tissue_or_organ_of_origin": ["Lung", "Liver", "Kidney"],
            },
        }

        # Simulate server restart: cache is cleared
        clear_all_session_caches()
        cache = get_session_cache(file_id)

        # Verify cache is empty (simulating post-restart state)
        assert not cache.has_any_pvs(), "Cache should be empty after clear"

        # When: Stage 4/5 lazy-loads PVs from disk
        with patch("src.domain.pv_persistence.get_file_store") as mock_get_store:
            mock_store = MagicMock()
            mock_store.load.return_value = pv_manifest_data
            mock_get_store.return_value = mock_store

            load_pv_manifest_from_disk(file_id, cache)

        # Then: PVs are available for validation and dropdowns
        assert cache.has_any_pvs(), "Cache should have PVs after loading"

        # Column mappings are restored
        assert cache.get_column_cde_key(0) == "primary_diagnosis_cde"
        assert cache.get_column_cde_key(1) == "tissue_or_organ_of_origin"

        # PV sets are restored as frozensets
        primary_pvs = cache.get_pvs_for_column(0)
        assert primary_pvs is not None
        assert "Adenocarcinoma" in primary_pvs
        assert "Squamous Cell Carcinoma" in primary_pvs

        tissue_pvs = cache.get_pvs_for_column(1)
        assert tissue_pvs is not None
        assert "Lung" in tissue_pvs
        assert len(tissue_pvs) == 3

    def test_missing_pv_manifest_degrades_gracefully(self) -> None:
        """FEATURE: Missing PV manifest doesn't crash; PV features just don't work.

        Simulates: User skips Stage 3 or manifest was never saved
        """
        # Given: No PV manifest exists on disk
        file_id = "no_manifest_file"
        clear_all_session_caches()
        cache = get_session_cache(file_id)

        # When: Attempting to load from non-existent manifest
        with patch("src.domain.pv_persistence.get_file_store") as mock_get_store:
            mock_store = MagicMock()
            mock_store.load.return_value = None  # No manifest found
            mock_get_store.return_value = mock_store

            # Then: No exception raised, cache remains empty
            load_pv_manifest_from_disk(file_id, cache)

        assert not cache.has_any_pvs(), "Cache should remain empty"
        assert cache.get_pvs_for_column(0) is None

    def test_new_upload_clears_stale_pv_cache(self) -> None:
        """FEATURE: Uploading a new file clears old PV data to prevent cross-contamination.

        Simulates: User completes workflow → uploads new file → should not see old PVs
        """
        # Given: A previous session has PVs in cache
        old_file_id = "old_file_abc"
        old_cache = get_session_cache(old_file_id)
        old_cache.set_pvs("some_cde", frozenset(["Old Value 1", "Old Value 2"]))
        assert old_cache.has_any_pvs()

        # When: User uploads a new file (Stage 1 clears caches)
        clear_all_session_caches()

        # Then: Old cache is cleared
        new_cache = get_session_cache(old_file_id)
        assert not new_cache.has_any_pvs(), "Old PV data should be cleared"

        # And new file gets a fresh cache
        new_file_id = "new_file_xyz"
        fresh_cache = get_session_cache(new_file_id)
        assert not fresh_cache.has_any_pvs(), "New file should have empty cache"

    def test_ensure_pvs_loaded_returns_cache_with_pvs(self) -> None:
        """FEATURE: ensure_pvs_loaded is a single entry point that handles lazy loading."""
        # Given: A file with PV manifest on disk
        file_id = "ensure_test_file"
        pv_manifest_data = {
            "column_to_cde_key": {"0": "cde1"},
            "pvs": {"cde1": ["Value A", "Value B"]},
        }

        clear_all_session_caches()

        # When: ensure_pvs_loaded is called
        with patch("src.domain.pv_persistence.get_file_store") as mock_get_store:
            mock_store = MagicMock()
            mock_store.load.return_value = pv_manifest_data
            mock_get_store.return_value = mock_store

            cache = ensure_pvs_loaded(file_id)

        # Then: Cache is returned with PVs loaded
        assert cache.has_any_pvs()
        assert cache.get_pvs_for_column(0) == frozenset(["Value A", "Value B"])


class TestSessionCacheThreadSafety:
    """Cache operations are thread-safe for concurrent async access."""

    def test_get_column_assignments_returns_copy(self) -> None:
        """Thread-safe accessor returns a copy, not the internal dict."""
        # Given: A cache with column assignments
        cache = SessionCache()
        cache.set_column_assignments({
            0: ColumnAssignment(0, "col_a", "cde1"),
            1: ColumnAssignment(1, "col_b", "cde2"),
        })
        assert cache.get_column_assignment(2) is None

        # When: Getting column assignments and modifying the returned dict
        assignments = cache.get_column_assignments()
        assignments[2] = ColumnAssignment(2, "col_c", "cde3")

        # Then: Cache is not affected by external mutation
        assert cache.get_column_assignment(2) is None, "Cache should not be modified"

    def test_pvs_stored_as_frozenset(self) -> None:
        """PVs are stored as frozensets for immutability and O(1) lookup."""
        # Given: A cache
        cache = SessionCache()

        # When: Setting PVs
        pv_list = ["Value A", "Value B", "Value C"]
        cache.set_pvs("test_cde", frozenset(pv_list))

        # Then: PVs are retrievable and membership check is O(1)
        pvs = cache.get_pvs_for_cde("test_cde")
        assert pvs is not None
        assert isinstance(pvs, frozenset)
        assert "Value A" in pvs
        assert "Unknown" not in pvs

    def test_batch_pv_update(self) -> None:
        """Multiple PV sets can be updated atomically."""
        # Given: An empty cache
        cache = SessionCache()

        # When: Batch updating PVs
        pv_map = {
            "cde1": frozenset(["A", "B"]),
            "cde2": frozenset(["X", "Y", "Z"]),
        }
        cache.set_pvs_batch(pv_map)

        # Then: All PVs are available
        assert cache.get_pvs_for_cde("cde1") == frozenset(["A", "B"])
        assert cache.get_pvs_for_cde("cde2") == frozenset(["X", "Y", "Z"])
        assert cache.has_any_pvs()


class TestPVLookupByColumn:
    """PV lookup via column index (through column_id→CDE mapping)."""

    def test_pvs_accessible_by_column_index(self) -> None:
        """Stage 4/5 look up PVs by column index, not name."""
        # Given: A cache with column assignments and PVs
        cache = SessionCache()
        cache.set_column_assignments({0: ColumnAssignment(0, "diagnosis", "primary_diagnosis_cde")})
        cache.set_pvs("primary_diagnosis_cde", frozenset(["Cancer", "Normal"]))

        # When: Looking up PVs by column index
        pvs = cache.get_pvs_for_column(0)

        # Then: PVs are found via the mapping
        assert pvs is not None
        assert "Cancer" in pvs
        assert "Normal" in pvs

    def test_unmapped_column_returns_none(self) -> None:
        """Columns without CDE mapping return None for PVs."""
        # Given: A cache with some assignments but not for all columns
        cache = SessionCache()
        cache.set_column_assignments({0: ColumnAssignment(0, "mapped_col", "some_cde")})
        cache.set_pvs("some_cde", frozenset(["Value"]))

        # When: Looking up PVs for an unmapped column index
        pvs = cache.get_pvs_for_column(1)

        # Then: Returns None (no PV validation for this column)
        assert pvs is None

    def test_column_mapped_but_no_pvs_returns_none(self) -> None:
        """Column mapped to CDE but CDE has no PVs (free-text field)."""
        # Given: A column assignment but no PVs for that CDE
        cache = SessionCache()
        cache.set_column_assignments({0: ColumnAssignment(0, "notes", "clinical_notes_cde")})
        # No PVs set for clinical_notes_cde (it's free-text)

        # When: Looking up PVs
        pvs = cache.get_pvs_for_column(0)

        # Then: Returns None (no conformance check needed)
        assert pvs is None
