"""Tests for normalize_manifest as a strict boundary validator.

normalize_manifest enforces the canonical wire shape at the SDK boundary:
{column_mappings: list[{column_name, cde_key, cde_id, alternatives} | None]}.
Any deviation raises MappingValidationError with structured context.
"""

from __future__ import annotations

import pytest
from netrias_client import MappingValidationError

from src.domain.harmonize import normalize_manifest


class TestNormalizeManifestBoundary:
    """Per plan §Contracts — rules (a) through (e)."""

    # (a) input is not a Mapping
    def test_rejects_non_mapping_input(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest("not a mapping")

    def test_rejects_list_input(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest([])

    # (b) Mapping lacks 'column_mappings' key
    def test_rejects_mapping_without_column_mappings(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({"other_key": []})

    # (c) 'column_mappings' is not a list
    def test_rejects_dict_format_column_mappings(self) -> None:
        # Legacy pre-0.4.0 dict-keyed-by-column-name shape must be rejected
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": {"diagnosis": {"targetField": "disease_type", "cde_id": 1}}
            })

    def test_rejects_non_list_column_mappings(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({"column_mappings": "oops"})

    # (d) entry is not None and not a dict with required keys/types
    def test_rejects_entry_missing_column_name(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": [
                    {"cde_key": "x", "cde_id": 1, "alternatives": []}
                ]
            })

    def test_rejects_entry_missing_cde_key(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": [
                    {"column_name": "dx", "cde_id": 1, "alternatives": []}
                ]
            })

    def test_rejects_entry_missing_cde_id(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": [
                    {"column_name": "dx", "cde_key": "x", "alternatives": []}
                ]
            })

    def test_rejects_entry_with_wrong_cde_id_type(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": [
                    {"column_name": "dx", "cde_key": "x", "cde_id": "not-an-int", "alternatives": []}
                ]
            })

    def test_rejects_entry_missing_alternatives(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": [
                    {"column_name": "dx", "cde_key": "x", "cde_id": 1}
                ]
            })

    # (e) alternative is not a dict with at least {target, confidence}
    def test_rejects_alternative_missing_target(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": [
                    {
                        "column_name": "dx", "cde_key": "x", "cde_id": 1,
                        "alternatives": [{"confidence": 0.9}],
                    }
                ]
            })

    def test_rejects_alternative_missing_confidence(self) -> None:
        with pytest.raises(MappingValidationError):
            normalize_manifest({
                "column_mappings": [
                    {
                        "column_name": "dx", "cde_key": "x", "cde_id": 1,
                        "alternatives": [{"target": "x"}],
                    }
                ]
            })

    # (f) harmonization required on every entry and every alternative
    def test_rejects_entry_missing_harmonization(self) -> None:
        with pytest.raises(MappingValidationError) as exc:
            normalize_manifest({
                "column_mappings": [
                    {
                        "column_name": "dx",
                        "cde_key": "disease_type",
                        "cde_id": 1,
                        "alternatives": [
                            {"target": "disease_type", "confidence": 0.85, "harmonization": "harmonizable"},
                        ],
                    }
                ]
            })
        assert "harmonization" in str(exc.value)

    def test_rejects_entry_with_unknown_harmonization_value(self) -> None:
        with pytest.raises(MappingValidationError) as exc:
            normalize_manifest({
                "column_mappings": [
                    {
                        "column_name": "dx",
                        "cde_key": "disease_type",
                        "cde_id": 1,
                        "harmonization": "totally-made-up",
                        "alternatives": [
                            {"target": "disease_type", "confidence": 0.85, "harmonization": "harmonizable"},
                        ],
                    }
                ]
            })
        assert "harmonization" in str(exc.value)

    def test_rejects_alternative_missing_harmonization(self) -> None:
        with pytest.raises(MappingValidationError) as exc:
            normalize_manifest({
                "column_mappings": [
                    {
                        "column_name": "dx",
                        "cde_key": "disease_type",
                        "cde_id": 1,
                        "harmonization": "harmonizable",
                        "alternatives": [
                            {"target": "disease_type", "confidence": 0.85},
                        ],
                    }
                ]
            })
        assert "harmonization" in str(exc.value)

    # Happy paths
    def test_accepts_canonical_manifest(self) -> None:
        manifest = {
            "column_mappings": [
                {
                    "column_name": "diagnosis",
                    "cde_key": "disease_type",
                    "cde_id": 323,
                    "harmonization": "harmonizable",
                    "alternatives": [
                        {"target": "disease_type", "confidence": 0.85, "cde_id": 323, "harmonization": "harmonizable"},
                    ],
                },
                None,
            ]
        }
        result = normalize_manifest(manifest)
        column_mappings = result["column_mappings"]
        assert len(column_mappings) == 2
        first = column_mappings[0]
        assert first is not None
        assert first["column_name"] == "diagnosis"
        assert first["cde_key"] == "disease_type"
        assert column_mappings[1] is None

    def test_accepts_empty_list(self) -> None:
        result = normalize_manifest({"column_mappings": []})
        assert result["column_mappings"] == []
