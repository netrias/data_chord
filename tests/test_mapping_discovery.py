"""Tests for MappingDiscoveryService — real NetriasClient integration."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from src.domain.manifest import ColumnMappingManifest, ManifestPayload
from src.domain.mapping_service import MappingDiscoveryService

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
    Then: the manifest is returned with stable column-key mappings for both columns
    """
    svc, mock_client = service_with_mock_client

    # Given: client returns a manifest with two mapped columns
    mock_client.discover_mapping_from_tabular.return_value = {
        "column_mappings": {
            "col_0000": {"cde_key": "organism_species", "cde_id": 131},
            "col_0001": {"cde_key": "primary_diagnosis", "cde_id": 2},
        }
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed,diagnosis\nLabrador,Cancer\n")

    # When
    discovery = svc.discover(csv_path=csv_path, data_model_key="ccdi", external_version_number="11.0.4")

    # Then: manifest contains both columns
    column_mappings = discovery.manifest_payload.get("column_mappings", {})
    assert "col_0000" in column_mappings
    assert "col_0001" in column_mappings
    assert column_mappings["col_0000"]["cde_key"] == "organism_species"
    assert column_mappings["col_0001"]["cde_key"] == "primary_diagnosis"
    mock_client.discover_mapping_from_tabular.assert_called_once()
    assert mock_client.discover_mapping_from_tabular.call_args.kwargs["target_version"] == "11.0.4"


def test_discover_passes_selected_target_version(
    service_with_mock_client: tuple[MappingDiscoveryService, MagicMock],
    tmp_path: Path,
) -> None:
    """
    Given: a selected model version from the Stage 1 popup
    When: MappingDiscoveryService.discover() is called with that version
    Then: the discovery API receives the same target_version
    """
    svc, mock_client = service_with_mock_client

    # Given
    mock_client.discover_mapping_from_tabular.return_value = {
        "column_mappings": {"col_0000": {"cde_key": "organism_species", "cde_id": 131}}
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed\nLabrador\n")
    assert mock_client.discover_mapping_from_tabular.call_count == 0

    # When
    svc.discover(csv_path=csv_path, data_model_key="ccdi", external_version_number="11.0.4")

    # Then
    assert mock_client.discover_mapping_from_tabular.call_args.kwargs["target_version"] == "11.0.4"


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
    Then: cde_targets has ModelSuggestion entries for both column keys
    """
    svc, mock_client = service_with_mock_client

    # Given
    mock_client.discover_mapping_from_tabular.return_value = {
        "column_mappings": {
            "col_0000": {"cde_key": "organism_species", "cde_id": 131},
            "col_0001": {"cde_key": "primary_diagnosis", "cde_id": 2},
        }
    }
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("breed,diagnosis\nLabrador,Cancer\n")

    # When
    discovery = svc.discover(csv_path=csv_path, data_model_key="ccdi", external_version_number="11.0.4")

    # Then: cde_targets has entries for both columns
    cde_targets = discovery.cde_targets
    assert "col_0000" in cde_targets
    assert "col_0001" in cde_targets
    assert cde_targets["col_0000"][0].target == "organism_species"
    assert cde_targets["col_0001"][0].target == "primary_diagnosis"


# ---------------------------------------------------------------------------
# Test 3: discover skips empty target fields
# ---------------------------------------------------------------------------


def test_discover_skips_empty_target_fields() -> None:
    """
    Given: a manifest where one column has cde_key="" and another is valid
    When: ColumnMappingManifest processes it
    Then: only the valid column appears in cde_targets
    """
    # Given
    manifest: ManifestPayload = {
        "column_mappings": {
            "breed": {"cde_key": "organism_species", "cde_id": 131},
            "empty_col": {"cde_key": "", "cde_id": 0},
        }
    }

    # When
    targets = ColumnMappingManifest.from_payload(manifest).suggestions_by_column()

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
        svc.discover(csv_path=Path("/fake.csv"), data_model_key="ccdi", external_version_number="11.0.4")


# ---------------------------------------------------------------------------
# Test 5: alternatives from manifest populate suggestions
# ---------------------------------------------------------------------------


def test_cde_targets_reads_alternatives_from_manifest() -> None:
    """
    Given: a manifest with alternatives (ranked suggestions) from the updated SDK
    When: ColumnMappingManifest processes it
    Then: cde_targets contains multiple ModelSuggestions per column, sorted by confidence
    """
    # Given
    manifest: ManifestPayload = {
        "column_mappings": {
            "age_col": {
                "cde_key": "age",
                "cde_id": 900,
                "alternatives": [
                    {"target": "age", "confidence": 1.0, "cde_id": 900},
                    {"target": "ageUnit", "confidence": 0.3, "cde_id": 904},
                ],
            },
            "sex_col": {
                "cde_key": "sex",
                "cde_id": 901,
                "alternatives": [
                    {"target": "sex", "confidence": 0.95, "cde_id": 901},
                ],
            },
        }
    }

    # When
    targets = ColumnMappingManifest.from_payload(manifest).suggestions_by_column()

    # Then: age_col has two suggestions
    assert len(targets["age_col"]) == 2
    assert targets["age_col"][0].target == "age"
    assert targets["age_col"][0].similarity == 1.0
    assert targets["age_col"][1].target == "ageUnit"
    assert targets["age_col"][1].similarity == 0.3

    # Then: sex_col has one suggestion
    assert len(targets["sex_col"]) == 1
    assert targets["sex_col"][0].target == "sex"


# ---------------------------------------------------------------------------
# Test 6: empty alternatives falls back to cde_key
# ---------------------------------------------------------------------------


def test_cde_targets_falls_back_to_target_field_when_alternatives_empty() -> None:
    """
    Given: a manifest where alternatives is present but all entries fail validation
    When: ColumnMappingManifest processes it
    Then: falls back to cde_key as a single suggestion
    """
    # Given: alternatives list has no valid entries (missing target key)
    manifest = cast(ManifestPayload, {
        "column_mappings": {
            "age_col": {
                "cde_key": "age",
                "cde_id": 900,
                "alternatives": [{"confidence": 0.9}],  # no "target" key
            },
        }
    })

    # When
    targets = ColumnMappingManifest.from_payload(manifest).suggestions_by_column()

    # Then: falls back to cde_key
    assert len(targets["age_col"]) == 1
    assert targets["age_col"][0].target == "age"
    assert targets["age_col"][0].similarity == 1.0


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
    mock_client.discover_mapping_from_tabular.side_effect = Exception("connection refused")
    csv_path = tmp_path / "test.csv"
    csv_path.write_text("a,b\n1,2\n")

    # When/Then
    with pytest.raises(RuntimeError, match="CDE discovery failed.*connection refused"):
        svc.discover(csv_path=csv_path, data_model_key="ccdi", external_version_number="11.0.4")


def test_discover_preserves_duplicate_headers_with_column_keys(
    service_with_mock_client: tuple[MappingDiscoveryService, MagicMock],
    tmp_path: Path,
) -> None:
    """
    Given: duplicate source headers and a Netrias response keyed by column keys
    When: discover() processes the response
    Then: returned mappings are keyed by stable source column keys
    """
    svc, mock_client = service_with_mock_client

    def _discover_mapping_from_tabular(
        *,
        source_path: Path,
        target_schema: str,
        target_version: str,
        confidence_threshold: float,
        sheet_name: str | None = None,
    ) -> dict[str, object]:
        assert source_path.name == "dupes.csv"
        assert target_version == "11.0.4"
        assert sheet_name is None
        return {
            "column_mappings": {
                "col_0000": {"cde_key": "first_name", "cde_id": 10},
                "col_0001": {"cde_key": "last_name", "cde_id": 11},
            }
        }

    mock_client.discover_mapping_from_tabular.side_effect = _discover_mapping_from_tabular
    csv_path = tmp_path / "dupes.csv"
    csv_path.write_text("name,name\nAlice,Smith\n")

    discovery = svc.discover(csv_path=csv_path, data_model_key="ccdi", external_version_number="11.0.4")

    column_mappings = discovery.manifest_payload.get("column_mappings", {})
    assert column_mappings["col_0000"]["cde_key"] == "first_name"
    assert column_mappings["col_0001"]["cde_key"] == "last_name"
    assert discovery.cde_targets["col_0000"][0].target == "first_name"
    assert discovery.cde_targets["col_0001"][0].target == "last_name"
