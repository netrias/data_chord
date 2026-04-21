"""Tests for MappingDiscoveryService — real NetriasClient integration."""

from __future__ import annotations

from pathlib import Path
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
            {"column_name": "breed", "cde_key": "organism_species", "cde_id": 131, "alternatives": [
                {"target": "organism_species", "confidence": 0.9, "cde_id": 131},
            ]},
            {"column_name": "diagnosis", "cde_key": "primary_diagnosis", "cde_id": 2, "alternatives": [
                {"target": "primary_diagnosis", "confidence": 0.85, "cde_id": 2},
            ]},
        ]
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed,diagnosis\nLabrador,Cancer\n")

    # When
    _, _, manifest = svc.discover(csv_path=csv_path, target_schema="ccdi")

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
    Then: cde_targets has ModelSuggestion entries for both columns
    """
    svc, mock_client = service_with_mock_client

    # Given
    mock_client.discover_mapping_from_csv.return_value = {
        "column_mappings": [
            {"column_name": "breed", "cde_key": "organism_species", "cde_id": 131, "alternatives": [
                {"target": "organism_species", "confidence": 0.9, "cde_id": 131},
            ]},
            {"column_name": "diagnosis", "cde_key": "primary_diagnosis", "cde_id": 2, "alternatives": [
                {"target": "primary_diagnosis", "confidence": 0.85, "cde_id": 2},
            ]},
        ]
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed,diagnosis\nLabrador,Cancer\n")

    # When
    cde_targets, _, _ = svc.discover(csv_path=csv_path, target_schema="ccdi")

    # Then: cde_targets has entries for both columns
    assert "breed" in cde_targets
    assert "diagnosis" in cde_targets
    assert cde_targets["breed"][0].target == "organism_species"
    assert cde_targets["diagnosis"][0].target == "primary_diagnosis"


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
    manifest: ManifestPayload = {
        "column_mappings": [
            {"column_name": "breed", "cde_key": "organism_species", "cde_id": 131, "alternatives": [
                {"target": "organism_species", "confidence": 0.9, "cde_id": 131},
            ]},
            None,
        ]
    }

    # When
    targets = _cde_targets_from_manifest(manifest)

    # Then: only breed appears
    assert "breed" in targets
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
    manifest: ManifestPayload = {
        "column_mappings": [
            {
                "column_name": "age_col",
                "cde_key": "age",
                "cde_id": 900,
                "alternatives": [
                    {"target": "age", "confidence": 1.0, "cde_id": 900},
                    {"target": "ageUnit", "confidence": 0.3, "cde_id": 904},
                ],
            },
            {
                "column_name": "sex_col",
                "cde_key": "sex",
                "cde_id": 901,
                "alternatives": [
                    {"target": "sex", "confidence": 0.95, "cde_id": 901},
                ],
            },
        ]
    }

    # When
    targets = _cde_targets_from_manifest(manifest)

    # Then: age_col has two suggestions
    assert len(targets["age_col"]) == 2
    assert targets["age_col"][0].target == "age"
    assert targets["age_col"][0].confidence == 1.0
    assert targets["age_col"][1].target == "ageUnit"
    assert targets["age_col"][1].confidence == 0.3

    # Then: sex_col has one suggestion
    assert len(targets["sex_col"]) == 1
    assert targets["sex_col"][0].target == "sex"


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
    Then: cde_targets is keyed by column_name and contains ranked alternatives
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
                "alternatives": [
                    {"target": "disease_type", "confidence": 0.85, "cde_id": 323},
                    {"target": "primary_diagnosis", "confidence": 0.72, "cde_id": 2},
                ],
            },
            None,
        ]
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("diagnosis,other\nCancer,x\n")

    # When
    cde_targets, _, _ = svc.discover(csv_path=csv_path, target_schema="ccdi")

    # Then
    assert "diagnosis" in cde_targets, f"expected 'diagnosis' in cde_targets, got {list(cde_targets)}"
    suggestions = cde_targets["diagnosis"]
    assert len(suggestions) == 2
    assert suggestions[0].target == "disease_type"
    assert suggestions[0].confidence == 0.85
    assert suggestions[1].target == "primary_diagnosis"
