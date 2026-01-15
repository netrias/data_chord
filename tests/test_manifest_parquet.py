"""Feature tests for manifest parquet read/write operations."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.domain.manifest import (
    ManifestRow,
    ManualOverride,
    PVAdjustment,
    get_manifest_schema,
    read_manifest_parquet,
)
from src.domain.manifest.writer import _write_manifest_parquet
from src.stage_5_review_summary.router import _manifest_to_json


class TestPVAdjustmentPersistence:
    """PV adjustments should survive parquet roundtrip (write then read)."""

    def test_pv_adjustment_roundtrip(self) -> None:
        """PV adjustment data is preserved when writing and reading manifest parquet."""

        # Given: Manifest rows with PV adjustments
        pv_adjustment = PVAdjustment(
            timestamp="2024-01-15T10:30:00Z",
            original_harmonization="Lung Carcinoma",
            adjusted_value="Lung Cancer",
            source="alternative_suggestion",
            user_id="pv_adjustment",
        )

        rows = [
            ManifestRow(
                job_id="test-job-123",
                column_id=0,
                column_name="diagnosis",
                to_harmonize="lung cancer",
                top_harmonization="Lung Carcinoma",
                ontology_id="NCIT:C3200",
                top_harmonizations=["Lung Carcinoma", "Lung Cancer", "Pulmonary Neoplasm"],
                confidence_score=0.85,
                error=None,
                row_indices=[0, 5, 12],
                manual_overrides=[],
                pv_adjustment=pv_adjustment,
            ),
            ManifestRow(
                job_id="test-job-123",
                column_id=1,
                column_name="treatment",
                to_harmonize="chemo",
                top_harmonization="Chemotherapy",
                ontology_id=None,
                top_harmonizations=["Chemotherapy"],
                confidence_score=0.99,
                error=None,
                row_indices=[0],
                manual_overrides=[],
                pv_adjustment=None,
            ),
        ]

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "test_manifest.parquet"

            # When: Manifest is written to parquet
            _write_manifest_parquet(manifest_path, rows)

            assert manifest_path.exists(), "Manifest parquet file was not created"

            # And: Manifest is read back from parquet
            summary = read_manifest_parquet(manifest_path)

            # Then: PV adjustment data is preserved
            assert summary is not None
            assert len(summary.rows) == 2

            # Row with PV adjustment
            row_with_pv = next(r for r in summary.rows if r.column_name == "diagnosis")
            assert row_with_pv.pv_adjustment is not None
            assert row_with_pv.pv_adjustment.original_harmonization == "Lung Carcinoma"
            assert row_with_pv.pv_adjustment.adjusted_value == "Lung Cancer"
            assert row_with_pv.pv_adjustment.source == "alternative_suggestion"
            assert row_with_pv.pv_adjustment.timestamp == "2024-01-15T10:30:00Z"

            # Row without PV adjustment
            row_without_pv = next(r for r in summary.rows if r.column_name == "treatment")
            assert row_without_pv.pv_adjustment is None

    def test_pv_adjustment_preserves_all_fields(self) -> None:
        """All PVAdjustment fields are preserved through roundtrip."""

        # Given: A PV adjustment with all fields populated including custom user_id
        pv_adjustment = PVAdjustment(
            timestamp="2024-06-20T14:45:30Z",
            original_harmonization="Original AI Value",
            adjusted_value="PV-Conformant Value",
            source="exact_match",
            user_id="custom_user_123",
        )

        row = ManifestRow(
            job_id="job-456",
            column_id=0,
            column_name="test_col",
            to_harmonize="input",
            top_harmonization="Original AI Value",
            ontology_id=None,
            top_harmonizations=["Original AI Value"],
            confidence_score=0.9,
            error=None,
            row_indices=[0],
            manual_overrides=[],
            pv_adjustment=pv_adjustment,
        )

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "test_manifest.parquet"

            # When: Written and read back
            _write_manifest_parquet(manifest_path, [row])
            summary = read_manifest_parquet(manifest_path)

            # Then: All fields match exactly
            assert summary is not None
            read_pv = summary.rows[0].pv_adjustment
            assert read_pv is not None
            assert read_pv.timestamp == pv_adjustment.timestamp
            assert read_pv.original_harmonization == pv_adjustment.original_harmonization
            assert read_pv.adjusted_value == pv_adjustment.adjusted_value
            assert read_pv.source == pv_adjustment.source
            assert read_pv.user_id == pv_adjustment.user_id

    def test_none_pv_adjustment_roundtrip(self) -> None:
        """Rows without PV adjustment read back as None."""

        # Given: A manifest row with no PV adjustment
        row = ManifestRow(
            job_id="job-789",
            column_id=0,
            column_name="col_without_pv",
            to_harmonize="value",
            top_harmonization="Value",
            ontology_id=None,
            top_harmonizations=["Value"],
            confidence_score=0.95,
            error=None,
            row_indices=[0],
            manual_overrides=[],
            pv_adjustment=None,
        )

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "test_manifest.parquet"

            # When: Written and read back
            _write_manifest_parquet(manifest_path, [row])
            summary = read_manifest_parquet(manifest_path)

            # Then: pv_adjustment is None
            assert summary is not None
            assert summary.rows[0].pv_adjustment is None


class TestManifestParquetSchema:
    """Manifest parquet schema includes pv_adjustment field."""

    def test_schema_includes_pv_adjustment_field(self) -> None:
        """The manifest schema defines pv_adjustment as a struct type."""

        # Given: The manifest schema definition
        schema = get_manifest_schema()

        # When: Field names are extracted
        field_names = [field.name for field in schema]

        # Then: pv_adjustment field exists
        assert "pv_adjustment" in field_names

    def test_pv_adjustment_struct_has_required_fields(self) -> None:
        """pv_adjustment struct contains all required fields."""

        # Given: The manifest schema
        schema = get_manifest_schema()

        # When: pv_adjustment field type is inspected
        pv_field = schema.field("pv_adjustment")
        type_str = str(pv_field.type)

        # Then: All PVAdjustment fields are present in the struct
        assert "timestamp" in type_str
        assert "original_harmonization" in type_str
        assert "adjusted_value" in type_str
        assert "source" in type_str
        assert "user_id" in type_str


class TestManifestToJson:
    """JSON conversion preserves all manifest data for human-readable download."""

    def test_manifest_to_json_preserves_nested_structures(self) -> None:
        """All fields including nested ManualOverride and PVAdjustment are serialized."""
        pv_adjustment = PVAdjustment(
            timestamp="2024-01-15T10:30:00Z",
            original_harmonization="Lung Carcinoma",
            adjusted_value="Lung Cancer",
            source="alternative_suggestion",
            user_id="pv_adjustment",
        )
        manual_overrides = [
            ManualOverride(user_id="user_1", timestamp="2024-01-14T09:00:00Z", value="First Edit"),
            ManualOverride(user_id="user_2", timestamp="2024-01-14T10:00:00Z", value="Second Edit"),
        ]

        row = ManifestRow(
            job_id="test-job-123",
            column_id=0,
            column_name="diagnosis",
            to_harmonize="lung cancer",
            top_harmonization="Lung Carcinoma",
            ontology_id="NCIT:C3200",
            top_harmonizations=["Lung Carcinoma", "Lung Cancer"],
            confidence_score=0.85,
            error=None,
            row_indices=[0, 5, 12],
            manual_overrides=manual_overrides,
            pv_adjustment=pv_adjustment,
        )

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "test_manifest.parquet"
            _write_manifest_parquet(manifest_path, [row])

            json_str = _manifest_to_json(manifest_path)

            assert json_str is not None
            data = json.loads(json_str)
            assert len(data) == 1

            parsed = data[0]
            assert parsed["job_id"] == "test-job-123"
            assert parsed["column_name"] == "diagnosis"
            assert parsed["confidence_score"] == 0.85
            assert parsed["row_indices"] == [0, 5, 12]

            assert len(parsed["manual_overrides"]) == 2
            assert parsed["manual_overrides"][0]["user_id"] == "user_1"
            assert parsed["manual_overrides"][1]["value"] == "Second Edit"

            assert parsed["pv_adjustment"]["original_harmonization"] == "Lung Carcinoma"
            assert parsed["pv_adjustment"]["adjusted_value"] == "Lung Cancer"

    def test_manifest_to_json_handles_none_values(self) -> None:
        """Null pv_adjustment and empty manual_overrides serialize correctly."""
        row = ManifestRow(
            job_id="job-789",
            column_id=0,
            column_name="col_without_extras",
            to_harmonize="value",
            top_harmonization="Value",
            ontology_id=None,
            top_harmonizations=["Value"],
            confidence_score=0.95,
            error=None,
            row_indices=[0],
            manual_overrides=[],
            pv_adjustment=None,
        )

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "test_manifest.parquet"
            _write_manifest_parquet(manifest_path, [row])

            json_str = _manifest_to_json(manifest_path)

            assert json_str is not None
            data = json.loads(json_str)
            parsed = data[0]

            assert parsed["manual_overrides"] == []
            assert parsed["pv_adjustment"] is None
            assert parsed["ontology_id"] is None
