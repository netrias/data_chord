"""Tests for MappingDiscoveryService — real NetriasClient integration."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from src.domain.manifest import ManifestPayload
from src.domain.mapping_service import MappingDiscoveryService, _cde_targets_from_manifest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service_with_mock_client(
    mock_client: MagicMock,
) -> tuple[MappingDiscoveryService, MagicMock]:
    """Inject a mock NetriasClient into MappingDiscoveryService."""
    svc = MappingDiscoveryService(mock_client)
    return svc, mock_client


# ---------------------------------------------------------------------------
# Test 1: discover returns manifest from client
# ---------------------------------------------------------------------------


def test_discover_returns_manifest_from_client(
    service_with_mock_client: tuple[MappingDiscoveryService, MagicMock],
    tmp_path: Path,
) -> None:
    """
    Given: a mocked NetriasClient returning a manifest with two column mappings
    When: MappingDiscoveryService.discover() is called
    Then: the manifest is returned with column_mappings for both columns
    """
    svc, mock_client = service_with_mock_client

    # Given: client returns a manifest with two mapped columns
    mock_client.discover_mapping_from_csv.return_value = {
        "column_mappings": [
            {
                "column_name": "breed", "cde_key": "organism_species", "cde_id": 131,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "organism_species", "confidence": 0.9, "cde_id": 131, "harmonization": "harmonizable"},
                ],
            },
            {
                "column_name": "diagnosis", "cde_key": "primary_diagnosis", "cde_id": 2,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "primary_diagnosis", "confidence": 0.85, "cde_id": 2, "harmonization": "harmonizable"},
                ],
            },
        ]
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed,diagnosis\nLabrador,Cancer\n")

    # When
    _, manifest = svc.discover(csv_path=csv_path, target_schema="ccdi")

    # Then: manifest contains both columns
    column_mappings = manifest.get("column_mappings", [])
    names = [e["column_name"] for e in column_mappings if e is not None]
    assert "breed" in names
    assert "diagnosis" in names
    breed_entry = next(e for e in column_mappings if e and e["column_name"] == "breed")
    diagnosis_entry = next(e for e in column_mappings if e and e["column_name"] == "diagnosis")
    assert breed_entry["cde_key"] == "organism_species"
    assert diagnosis_entry["cde_key"] == "primary_diagnosis"


# ---------------------------------------------------------------------------
# Test 2: discover builds cde_targets from manifest
# ---------------------------------------------------------------------------


def test_discover_builds_cde_targets_from_manifest(
    service_with_mock_client: tuple[MappingDiscoveryService, MagicMock],
    tmp_path: Path,
) -> None:
    """
    Given: a manifest with columns "breed" and "diagnosis" mapped to CDEs
    When: discover() processes it
    Then: cde_targets has ModelSuggestion entries for both column positions
    """
    svc, mock_client = service_with_mock_client

    # Given
    mock_client.discover_mapping_from_csv.return_value = {
        "column_mappings": [
            {
                "column_name": "breed", "cde_key": "organism_species", "cde_id": 131,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "organism_species", "confidence": 0.9, "cde_id": 131, "harmonization": "harmonizable"},
                ],
            },
            {
                "column_name": "diagnosis", "cde_key": "primary_diagnosis", "cde_id": 2,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "primary_diagnosis", "confidence": 0.85, "cde_id": 2, "harmonization": "harmonizable"},
                ],
            },
        ]
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed,diagnosis\nLabrador,Cancer\n")

    # When
    cde_targets, _ = svc.discover(csv_path=csv_path, target_schema="ccdi")

    # Then: cde_targets has entries for both column IDs
    assert "0" in cde_targets
    assert "1" in cde_targets
    assert cde_targets["0"][0].target == "organism_species"
    assert cde_targets["1"][0].target == "primary_diagnosis"


# ---------------------------------------------------------------------------
# Test 3: discover skips empty target fields
# ---------------------------------------------------------------------------


def test_discover_skips_none_entries() -> None:
    """
    Given: a canonical list-format manifest where one entry is None (unmapped column)
    When: _cde_targets_from_manifest processes it
    Then: only the non-None entry appears in cde_targets
    """
    # Given
    manifest = cast(ManifestPayload, {
        "column_mappings": [
            {
                "column_name": "breed", "cde_key": "organism_species", "cde_id": 131,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "organism_species", "confidence": 0.9, "cde_id": 131, "harmonization": "harmonizable"},
                ],
            },
            None,
        ]
    })

    # When
    targets = _cde_targets_from_manifest(manifest)

    # Then: only column 0 appears
    assert "0" in targets
    assert len(targets) == 1


# ---------------------------------------------------------------------------
# Test 4: discover raises when client unavailable
# ---------------------------------------------------------------------------


def test_discover_raises_when_client_unavailable() -> None:
    """
    Given: MappingDiscoveryService with no client (None)
    When: discover() is called
    Then: RuntimeError is raised
    """
    # Given: no client → None
    svc = MappingDiscoveryService(None)
    assert svc._client is None

    # When/Then
    with pytest.raises(RuntimeError, match="NetriasClient unavailable"):
        svc.discover(csv_path=Path("/fake.csv"), target_schema="ccdi")


# ---------------------------------------------------------------------------
# Test 5: alternatives from manifest populate suggestions
# ---------------------------------------------------------------------------


def test_cde_targets_reads_alternatives_from_manifest() -> None:
    """
    Given: a canonical list-format manifest with alternatives (ranked suggestions)
    When: _cde_targets_from_manifest processes it
    Then: cde_targets contains multiple ModelSuggestions per column, sorted by confidence
    """
    # Given
    manifest = cast(ManifestPayload, {
        "column_mappings": [
            {
                "column_name": "age_col",
                "cde_key": "age",
                "cde_id": 900,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "age", "confidence": 1.0, "cde_id": 900, "harmonization": "harmonizable"},
                    {"target": "ageUnit", "confidence": 0.3, "cde_id": 904, "harmonization": "harmonizable"},
                ],
            },
            {
                "column_name": "sex_col",
                "cde_key": "sex",
                "cde_id": 901,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "sex", "confidence": 0.95, "cde_id": 901, "harmonization": "harmonizable"},
                ],
            },
        ]
    })

    # When
    targets = _cde_targets_from_manifest(manifest)

    # Then: column 0 has two suggestions
    assert len(targets["0"]) == 2
    assert targets["0"][0].target == "age"
    assert targets["0"][0].confidence == 1.0
    assert targets["0"][1].target == "ageUnit"
    assert targets["0"][1].confidence == 0.3

    # Then: column 1 has one suggestion
    assert len(targets["1"]) == 1
    assert targets["1"][0].target == "sex"


def test_duplicate_header_recommendations_are_keyed_by_column_position() -> None:
    """
    Given: a manifest with duplicate header names but different recommendations
    When: _cde_targets_from_manifest processes it
    Then: each duplicate column keeps its own suggestions under its column_id key
    """
    # Given
    manifest = cast(ManifestPayload, {
        "column_mappings": [
            {
                "column_name": "sample_id",
                "cde_key": "left_sample",
                "cde_id": 1,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "left_sample", "confidence": 0.9, "cde_id": 1, "harmonization": "harmonizable"},
                ],
            },
            {
                "column_name": "sample_id",
                "cde_key": "right_sample",
                "cde_id": 2,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "right_sample", "confidence": 0.8, "cde_id": 2, "harmonization": "harmonizable"},
                ],
            },
        ]
    })

    # When
    targets = _cde_targets_from_manifest(manifest)

    # Then
    assert targets["0"][0].target == "left_sample"
    assert targets["1"][0].target == "right_sample"


# ---------------------------------------------------------------------------
# Test 7: discover wraps SDK errors as RuntimeError
# ---------------------------------------------------------------------------


def test_discover_wraps_sdk_errors_as_runtime_error(
    service_with_mock_client: tuple[MappingDiscoveryService, MagicMock],
    tmp_path: Path,
) -> None:
    """
    Given: a NetriasClient that raises an exception during discovery
    When: discover() is called
    Then: RuntimeError is raised wrapping the original error
    """
    svc, mock_client = service_with_mock_client

    # Given: client raises
    mock_client.discover_mapping_from_csv.side_effect = Exception("connection refused")
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("a,b\n1,2\n")

    # When/Then
    with pytest.raises(RuntimeError, match="CDE discovery failed.*connection refused"):
        svc.discover(csv_path=csv_path, target_schema="ccdi")


# ---------------------------------------------------------------------------
# Test 8: SDK canonical list shape surfaces as cde_targets
# ---------------------------------------------------------------------------


def test_recommendations_surface_from_sdk(
    service_with_mock_client: tuple[MappingDiscoveryService, MagicMock],
    tmp_path: Path,
) -> None:
    """
    Given: SDK returns canonical list-format manifest with one real entry
    When: discover() processes it
    Then: cde_targets is keyed by column_id and contains ranked alternatives
          built from the canonical `confidence` field
    """
    svc, mock_client = service_with_mock_client

    # Given: canonical list-format (matches netrias_client >=0.4.1 wire shape)
    mock_client.discover_mapping_from_csv.return_value = {
        "column_mappings": [
            {
                "column_name": "diagnosis",
                "cde_key": "disease_type",
                "cde_id": 323,
                "harmonization": "harmonizable",
                "alternatives": [
                    {"target": "disease_type", "confidence": 0.85, "cde_id": 323, "harmonization": "harmonizable"},
                    {"target": "primary_diagnosis", "confidence": 0.72, "cde_id": 2, "harmonization": "harmonizable"},
                ],
            },
            None,
        ]
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("diagnosis,other\nCancer,x\n")

    # When
    cde_targets, _ = svc.discover(csv_path=csv_path, target_schema="ccdi")

    # Then
    assert "0" in cde_targets, f"expected column id '0' in cde_targets, got {list(cde_targets)}"
    suggestions = cde_targets["0"]
    assert len(suggestions) == 2
    assert suggestions[0].target == "disease_type"
    assert suggestions[0].confidence == 0.85
    assert suggestions[1].target == "primary_diagnosis"


# ---------------------------------------------------------------------------
# Test 9: harmonization surfaces onto ModelSuggestion
# ---------------------------------------------------------------------------


def test_model_suggestion_carries_harmonization_from_alternative(
    service_with_mock_client: tuple[MappingDiscoveryService, MagicMock],
    tmp_path: Path,
) -> None:
    """
    Given: SDK returns a manifest whose top alternative is 'no_permissible_values'
    When: MappingDiscoveryService.discover() is invoked
    Then: the produced ModelSuggestion.harmonization propagates the enum value
    """
    svc, mock_client = service_with_mock_client

    mock_client.discover_mapping_from_csv.return_value = {
        "column_mappings": [
            {
                "column_name": "middle_name",
                "cde_key": "middle_name",
                "cde_id": 316,
                "harmonization": "no_permissible_values",
                "alternatives": [
                    {
                        "target": "middle_name", "confidence": 1.0,
                        "cde_id": 316, "harmonization": "no_permissible_values",
                    },
                    {
                        "target": "last_name", "confidence": 0.6,
                        "cde_id": 317, "harmonization": "no_permissible_values",
                    },
                ],
            },
        ]
    }
    csv_path = tmp_path / "names.csv"
    csv_path.write_text("middle_name\nAnn\n")

    cde_targets, _ = svc.discover(csv_path=csv_path, target_schema="gc")

    suggestions = cde_targets["0"]
    assert len(suggestions) == 2
    assert suggestions[0].harmonization == "no_permissible_values"
    assert suggestions[1].harmonization == "no_permissible_values"
