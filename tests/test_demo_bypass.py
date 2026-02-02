"""Feature tests for demo bypass discovery and CDE injection."""

from __future__ import annotations

from pathlib import Path

from src.domain.demo_bypass import (
    DEMO_CDE_REGISTRY,
    discover_bypass,
    get_demo_cde_infos,
)
from tests.conftest import TEST_TARGET_SCHEMA


def test_discover_bypass_maps_known_columns(sample_csv_path: Path) -> None:
    """Known CSV headers produce suggestions and manifest entries."""

    # Given: a CSV with headers matching the demo registry
    # (sample.csv has: record_id, therapeutic_agents, primary_diagnosis,
    #  morphology, tissue_or_organ_of_origin, sample_anatomic_site)
    assert sample_csv_path.exists()

    # When: discover_bypass processes the file
    cde_targets, manual_overrides, manifest = discover_bypass(sample_csv_path, TEST_TARGET_SCHEMA)

    # Then: known columns are mapped; unknown columns (record_id) are omitted
    assert "therapeutic_agents" in cde_targets
    assert "primary_diagnosis" in cde_targets
    assert "morphology" in cde_targets
    assert "record_id" not in cde_targets

    # Then: manual_overrides is always empty (bypass doesn't produce overrides)
    assert manual_overrides == {}

    # Then: manifest contains column_mappings for mapped columns
    column_mappings = manifest.get("column_mappings", {})
    assert "therapeutic_agents" in column_mappings
    entry = column_mappings["therapeutic_agents"]
    assert entry.get("targetField") == "therapeutic_agents"
    assert entry.get("cde_id") == 1


def test_discover_bypass_returns_high_confidence_suggestions(sample_csv_path: Path) -> None:
    """Hardcoded mappings produce similarity=1.0 since they're exact matches."""

    # Given: a CSV with known headers
    assert sample_csv_path.exists()

    # When: discover_bypass processes the file
    cde_targets, _, _ = discover_bypass(sample_csv_path, TEST_TARGET_SCHEMA)

    # Then: all suggestions have similarity=1.0
    for suggestions in cde_targets.values():
        assert len(suggestions) == 1
        assert suggestions[0].similarity == 1.0


def test_discover_bypass_empty_csv(tmp_path: Path) -> None:
    """Empty CSV produces no mappings."""

    # Given: an empty CSV file
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("")

    # When: discover_bypass processes it
    cde_targets, manual_overrides, manifest = discover_bypass(empty_csv, TEST_TARGET_SCHEMA)

    # Then: no mappings are produced
    assert cde_targets == {}
    assert manual_overrides == {}
    assert manifest.get("column_mappings", {}) == {}


def test_discover_bypass_unrecognized_columns(tmp_path: Path) -> None:
    """CSV with no recognized headers produces no mappings."""

    # Given: a CSV with only unrecognized headers
    csv_path = tmp_path / "unknown.csv"
    csv_path.write_text("foo,bar,baz\n1,2,3\n", encoding="utf-8")

    # When: discover_bypass processes the file
    cde_targets, _, manifest = discover_bypass(csv_path, TEST_TARGET_SCHEMA)

    # Then: nothing is mapped
    assert cde_targets == {}
    assert manifest.get("column_mappings", {}) == {}


def test_get_demo_cde_infos_returns_deduplicated_cdes() -> None:
    """Multiple column variants mapping to the same CDE should produce one CDEInfo."""

    # Given: the registry has multiple entries for the same CDE
    # (e.g., "therapeutic_agents" and "therapeutic agents" both map to CDE ID 1)
    assert ("therapeutic_agents", 1) == DEMO_CDE_REGISTRY["therapeutic_agents"]
    assert ("therapeutic_agents", 1) == DEMO_CDE_REGISTRY["therapeutic agents"]

    # When: CDEInfo objects are generated
    cde_infos = get_demo_cde_infos(version_label="v1")

    # Then: deduplicated by cde_id — one entry per unique CDE
    cde_ids = [c.cde_id for c in cde_infos]
    assert len(cde_ids) == len(set(cde_ids)), "CDEInfo list should be deduplicated by cde_id"

    # Then: all expected CDE IDs are present
    expected_ids = {1, 2, 3, 4, 5}
    assert set(cde_ids) == expected_ids


def test_get_demo_cde_infos_uses_version_label() -> None:
    """Version label is passed through to all CDEInfo objects."""

    # Given: a specific version label
    version = "v42"

    # When: CDEInfo objects are generated
    cde_infos = get_demo_cde_infos(version_label=version)

    # Then: all use the provided version
    for cde in cde_infos:
        assert cde.version_label == version


def test_discover_bypass_bom_csv(tmp_path: Path) -> None:
    """CSV with BOM (Excel export) has headers read correctly."""

    # Given: a CSV with UTF-8 BOM that has a known header
    csv_path = tmp_path / "bom.csv"
    csv_path.write_bytes(b"\xef\xbb\xbfmorphology,other\nAdenocarcinoma,x\n")

    # When: discover_bypass processes the file
    cde_targets, _, manifest = discover_bypass(csv_path, TEST_TARGET_SCHEMA)

    # Then: BOM-stripped header "morphology" is recognized
    assert "morphology" in cde_targets
    assert "morphology" in manifest.get("column_mappings", {})
