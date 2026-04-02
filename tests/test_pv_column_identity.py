"""Feature tests for column-by-index PV cache identity.

Verifies that PV lookups use integer column positions (not names), enabling
correct validation when duplicate-named columns share a CSV file.
"""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import MagicMock, patch

from src.domain.column_assignment import ColumnAssignment
from src.domain.data_model_cache import SessionCache, clear_all_session_caches, get_session_cache
from src.domain.manifest import ManifestPayload
from src.domain.pv_persistence import load_pv_manifest_from_disk, save_pv_manifest_to_disk
from src.stage_3_harmonize.router import _store_column_mappings_in_cache


class TestDuplicateColumnNameIdentity:
    """Columns at different positions get independent cache entries even when names collide."""

    def test_duplicate_column_names_get_independent_pvs(self) -> None:
        """
        Given: Two columns with the same name at positions 0 and 1
        When: _store_column_mappings_in_cache is called
        Then: Both column_ids are registered in the cache (neither is silently dropped)
        """
        # Given
        cache = SessionCache()
        cache.set_pvs("cde_a", frozenset(["PV1", "PV2"]))
        csv_headers = ["col", "col"]  # duplicate name at positions 0 and 1
        manifest = cast(ManifestPayload, {"column_mappings": {"col": {"targetField": "cde_a"}}})
        assert cache.get_pvs_for_column(0) is None, "No PVs loaded yet"

        # When
        _store_column_mappings_in_cache(cache, manifest, {}, csv_headers)

        # Then: both positions get the mapping (neither is dropped)
        assert cache.get_column_cde_key(0) == "cde_a"
        assert cache.get_column_cde_key(1) == "cde_a"
        assert cache.get_pvs_for_column(0) == frozenset(["PV1", "PV2"])
        assert cache.get_pvs_for_column(1) == frozenset(["PV1", "PV2"])


class TestPVManifestJsonRoundTrip:
    """Integer column_id keys survive JSON serialization and deserialization."""

    def test_int_keys_survive_json_round_trip(self) -> None:
        """
        Given: A cache with integer-keyed column_to_cde_key mappings
        When: save_pv_manifest_to_disk then load_pv_manifest_from_disk
        Then: Integer keys are preserved after the JSON round-trip
        """
        # Given
        file_id = "round_trip_test"
        clear_all_session_caches()
        cache = get_session_cache(file_id)
        cache.set_column_assignments({
            0: ColumnAssignment(0, "col_zero", "cde_key_zero"),
            1: ColumnAssignment(1, "col_one", "cde_key_one"),
        })
        cache.set_pvs("cde_key_zero", frozenset(["A"]))
        cache.set_pvs("cde_key_one", frozenset(["B"]))
        pv_map = {"cde_key_zero": frozenset(["A"]), "cde_key_one": frozenset(["B"])}

        saved_data: dict[str, Any] = {}

        def fake_save(file_id_: str, file_type_: object, data: dict[str, Any]) -> None:
            saved_data.update(data)

        def fake_load(file_id_: str, file_type_: object) -> dict[str, Any]:
            return saved_data

        with patch("src.domain.pv_persistence.get_file_store") as mock_store_factory:
            mock_store = MagicMock()
            mock_store.save.side_effect = fake_save
            mock_store.load.side_effect = fake_load
            mock_store_factory.return_value = mock_store

            save_pv_manifest_to_disk(file_id, cache, pv_map)

            # Simulate JSON round-trip (int keys become strings in JSON)
            json_str = json.dumps(saved_data)
            loaded_data = json.loads(json_str)
            mock_store.load.side_effect = lambda *_: loaded_data

            new_cache = SessionCache()
            load_pv_manifest_from_disk(file_id, new_cache)

        # Then: integer keys survived the round-trip
        assert new_cache.get_column_cde_key(0) == "cde_key_zero"
        assert new_cache.get_column_cde_key(1) == "cde_key_one"
        assert new_cache.get_column_assignment(0) == ColumnAssignment(0, "col_zero", "cde_key_zero")
        assert new_cache.get_column_assignment(1) == ColumnAssignment(1, "col_one", "cde_key_one")
        assert new_cache.get_pvs_for_column(0) == frozenset(["A"])
        assert new_cache.get_pvs_for_column(1) == frozenset(["B"])


class TestAbsentColumnIndexSkipped:
    """Out-of-range column index overrides are silently skipped."""

    def test_out_of_range_column_index_is_skipped(self) -> None:
        """
        Given: A manual override for an out-of-range column index not present in the CSV
        When: _store_column_mappings_in_cache is called
        Then: No KeyError; out-of-range index is not in the cache
        """
        # Given
        cache = SessionCache()
        manual_overrides = {99: "some_cde"}
        csv_headers = ["col_a", "col_b"]
        assert cache.get_column_cde_key(0) is None, "Cache starts empty"

        # When
        _store_column_mappings_in_cache(cache, None, manual_overrides, csv_headers)

        # Then: no error, and no entries were added for the out-of-range index
        assert cache.get_column_cde_key(0) is None
        assert cache.get_column_cde_key(1) is None
