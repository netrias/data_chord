"""Tests for _apply_column_mappings with canonical list-format output.

Verifies that resolved assignments produce a position-keyed list of
ColumnMappingRecord entries in the canonical `{column_name, cde_key,
cde_id, alternatives}` shape; None for unmapped columns.
"""

from __future__ import annotations

from typing import cast

from src.domain.cde import CDEInfo
from src.domain.column_assignment import ColumnAssignment
from src.domain.data_model_cache import SessionCache
from src.domain.harmonize import _apply_column_mappings
from src.domain.manifest import ManifestPayload


def _cache_with_cdes(*cde_keys: str) -> SessionCache:
    """Session cache with mock CDEs for lookup."""
    cache = SessionCache()
    cdes = [
        CDEInfo(cde_id=i, cde_key=key, description=None, version_label="1")
        for i, key in enumerate(cde_keys, start=1)
    ]
    cache.set_cdes(cdes, data_model_key="test", version_label="1")
    return cache


class TestCanonicalColumnMappings:
    """_apply_column_mappings produces canonical list-format entries."""

    def test_writes_canonical_shape(self) -> None:
        """Entries carry column_name/cde_key/cde_id/alternatives; no legacy keys."""
        # Given: two mapped columns, manifest pre-populated from discovery
        manifest = cast(ManifestPayload, {"column_mappings": [
            {"column_name": "dx", "cde_key": "dx_cde", "cde_id": 1, "harmonization": "harmonizable",
             "alternatives": [{"target": "dx_cde", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"}]},
            {"column_name": "age", "cde_key": "age_cde", "cde_id": 2, "harmonization": "harmonizable",
             "alternatives": [{"target": "age_cde", "confidence": 0.85, "cde_id": 2, "harmonization": "harmonizable"}]},
        ]})
        assignments = {
            0: ColumnAssignment(0, "dx", "dx_cde"),
            1: ColumnAssignment(1, "age", "age_cde"),
        }
        cache = _cache_with_cdes("dx_cde", "age_cde")

        # When
        _apply_column_mappings(manifest, assignments, cache)

        # Then: entries are canonical shape
        entries = manifest.get("column_mappings")
        assert isinstance(entries, list)
        assert len(entries) == 2
        for entry in entries:
            assert entry is not None
            assert set(entry.keys()) >= {"column_name", "cde_key", "cde_id", "alternatives"}
            # No legacy keys leak into output
            assert "name" not in entry
            assert "targetField" not in entry
            assert "route" not in entry
        first, second = entries[0], entries[1]
        assert first is not None and second is not None
        assert first["column_name"] == "dx"
        assert first["cde_key"] == "dx_cde"
        assert second["column_name"] == "age"
        assert second["cde_key"] == "age_cde"

    def test_mapped_columns_produce_entries_at_correct_positions(self) -> None:
        """Each mapped assignment creates an entry at its column_id index."""
        # Given: manifest pre-populated from discovery so harmonization is available
        manifest = cast(ManifestPayload, {"column_mappings": [
            {"column_name": "dx", "cde_key": "dx_cde", "cde_id": 1, "harmonization": "harmonizable",
             "alternatives": [{"target": "dx_cde", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"}]},
            {"column_name": "age", "cde_key": "age_cde", "cde_id": 2, "harmonization": "harmonizable",
             "alternatives": [{"target": "age_cde", "confidence": 0.85, "cde_id": 2, "harmonization": "harmonizable"}]},
        ]})
        assignments = {
            0: ColumnAssignment(0, "dx", "dx_cde"),
            1: ColumnAssignment(1, "age", "age_cde"),
        }
        cache = _cache_with_cdes("dx_cde", "age_cde")

        # When
        _apply_column_mappings(manifest, assignments, cache)

        # Then
        entries = manifest.get("column_mappings")
        assert isinstance(entries, list)
        assert len(entries) == 2
        assert entries[0] is not None and entries[0]["cde_key"] == "dx_cde"
        assert entries[1] is not None and entries[1]["cde_key"] == "age_cde"

    def test_unmapped_column_is_none(self) -> None:
        """Unmapped column produces None at that index."""
        # Given: col_0 mapped, col_1 unmapped; manifest pre-populated from discovery
        manifest = cast(ManifestPayload, {"column_mappings": [
            {"column_name": "dx", "cde_key": "dx_cde", "cde_id": 1, "harmonization": "harmonizable",
             "alternatives": [{"target": "dx_cde", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"}]},
            None,
        ]})
        assignments = {
            0: ColumnAssignment(0, "dx", "dx_cde"),
            1: ColumnAssignment(1, "age", None),
        }
        cache = _cache_with_cdes("dx_cde")

        # When
        _apply_column_mappings(manifest, assignments, cache)

        # Then
        entries = manifest.get("column_mappings")
        assert isinstance(entries, list)
        assert entries[0] is not None and entries[0]["cde_key"] == "dx_cde"
        assert entries[1] is None

    def test_duplicate_headers_get_independent_entries(self) -> None:
        """Duplicate column names at different positions get independent entries."""
        # Given: col_0 and col_1 both named "dx", col_0 mapped, col_1 unmapped;
        # manifest pre-populated from discovery so harmonization is available
        manifest = cast(ManifestPayload, {"column_mappings": [
            {
                "column_name": "dx", "cde_key": "primary_dx", "cde_id": 1,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "primary_dx", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"},
                ],
            },
            None,
            {
                "column_name": "age", "cde_key": "age_cde", "cde_id": 2,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "age_cde", "confidence": 0.85, "cde_id": 2, "harmonization": "harmonizable"},
                ],
            },
        ]})
        assignments = {
            0: ColumnAssignment(0, "dx", "primary_dx"),
            1: ColumnAssignment(1, "dx", None),
            2: ColumnAssignment(2, "age", "age_cde"),
        }
        cache = _cache_with_cdes("primary_dx", "age_cde")

        # When
        _apply_column_mappings(manifest, assignments, cache)

        # Then
        entries = manifest.get("column_mappings")
        assert isinstance(entries, list)
        assert len(entries) == 3
        assert entries[0] is not None and entries[0]["cde_key"] == "primary_dx"
        assert entries[1] is None
        assert entries[2] is not None and entries[2]["cde_key"] == "age_cde"

    def test_preserves_alternatives_from_existing_entry(self) -> None:
        """Alternatives from existing entry are carried forward."""
        # Given: manifest has an entry with alternatives that should persist
        manifest = cast(ManifestPayload, {
            "column_mappings": [
                {
                    "column_name": "dx",
                    "cde_key": "dx_cde",
                    "cde_id": 1,
                    "harmonization": "harmonizable",
                    "alternatives": [
                        {"target": "dx_cde", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"},
                        {"target": "alt_cde", "confidence": 0.6, "cde_id": 7, "harmonization": "harmonizable"},
                    ],
                },
                None,
            ],
        })
        assignments = {
            0: ColumnAssignment(0, "dx", "dx_cde"),
            1: ColumnAssignment(1, "age", None),
        }
        cache = _cache_with_cdes("dx_cde")

        # When
        _apply_column_mappings(manifest, assignments, cache)

        # Then
        entries = manifest.get("column_mappings")
        assert isinstance(entries, list)
        assert entries[0] is not None
        alternatives = entries[0]["alternatives"]
        assert len(alternatives) == 2
        assert alternatives[0]["target"] == "dx_cde"
        assert alternatives[0]["confidence"] == 0.9

    def test_empty_assignments_is_noop(self) -> None:
        """Empty assignments don't modify the manifest."""
        # Given
        original = [None, None]
        manifest = cast(ManifestPayload, {"column_mappings": original})

        # When
        _apply_column_mappings(manifest, {}, SessionCache())

        # Then
        assert manifest.get("column_mappings") is original
