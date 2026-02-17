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
        "column_mappings": {
            "breed": {"targetField": "organism_species", "cde_id": 131},
            "diagnosis": {"targetField": "primary_diagnosis", "cde_id": 2},
        }
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed,diagnosis\nLabrador,Cancer\n")

    # When
    _, _, manifest = svc.discover(csv_path=csv_path, target_schema="ccdi")

    # Then: manifest contains both columns
    column_mappings = manifest.get("column_mappings", {})
    assert "breed" in column_mappings
    assert "diagnosis" in column_mappings
    assert column_mappings["breed"]["targetField"] == "organism_species"
    assert column_mappings["diagnosis"]["targetField"] == "primary_diagnosis"


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
        "column_mappings": {
            "breed": {"targetField": "organism_species", "cde_id": 131},
            "diagnosis": {"targetField": "primary_diagnosis", "cde_id": 2},
        }
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


def test_discover_skips_empty_target_fields() -> None:
    """
    Given: a manifest where one column has targetField="" and another is valid
    When: _cde_targets_from_manifest processes it
    Then: only the valid column appears in cde_targets
    """
    # Given
    manifest: ManifestPayload = {
        "column_mappings": {
            "breed": {"targetField": "organism_species", "cde_id": 131},
            "empty_col": {"targetField": "", "cde_id": 0},
        }
    }

    # When
    targets = _cde_targets_from_manifest(manifest)

    # Then: only breed appears
    assert "breed" in targets
    assert "empty_col" not in targets


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
# Test 5: discover wraps SDK errors as RuntimeError
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
