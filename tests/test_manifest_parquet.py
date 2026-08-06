"""Feature tests for manifest parquet read/write operations."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq

from src.domain.manifest import (
    ManifestRow,
    ManualOverride,
)
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_schema import get_manifest_schema
from src.persistence.manifest_writer import _write_manifest_parquet
from src.stage_5_review_summary.use_cases import _manifest_to_json


class TestManifestParquetSchema:
    """Manifest parquet schema structure."""

    def test_schema_includes_manual_overrides_field(self) -> None:
        """The manifest schema defines manual_overrides as a list of structs."""

        # Given: The manifest schema definition
        schema = get_manifest_schema()

        # When: Field names are extracted
        field_names = [field.name for field in schema]

        # Then: manual_overrides field exists
        assert "manual_overrides" in field_names

    def test_schema_does_not_include_pv_adjustment(self) -> None:
        """pv_adjustment was removed — schema should not contain it."""

        schema = get_manifest_schema()
        field_names = [field.name for field in schema]

        assert "pv_adjustment" not in field_names

    def test_schema_preserves_the_provider_field_types(self) -> None:
        schema = get_manifest_schema()
        override_struct = pa.struct([
            ("user_id", pa.string()),
            ("timestamp", pa.string()),
            ("value", pa.string()),
        ])

        assert schema == pa.schema([
            ("job_id", pa.string()),
            ("column_id", pa.int64()),
            ("column_name", pa.string()),
            ("to_harmonize", pa.string()),
            ("top_harmonization", pa.string()),
            ("ontology_id", pa.string()),
            ("top_harmonizations", pa.list_(pa.string())),
            ("confidence_score", pa.float64()),
            ("error", pa.string()),
            ("row_indices", pa.list_(pa.int64())),
            ("manual_overrides", pa.list_(override_struct)),
        ])

    def test_reader_rejects_partial_provider_schema(self, tmp_path: Path) -> None:
        """A valid Parquet container is not a valid harmonization manifest by accident."""
        path = tmp_path / "partial.parquet"
        pq.write_table(pa.table({"job_id": ["job-1"]}), path)

        assert read_manifest_parquet(path) is None

    def test_reader_accepts_provider_numeric_types_and_absent_overrides(self, tmp_path: Path) -> None:
        """Read the manifest shape returned by the live harmonization provider."""
        path = tmp_path / "provider.parquet"
        table = pa.table({
            "job_id": pa.array(["job-1"], type=pa.string()),
            "column_id": pa.array([3], type=pa.int32()),
            "column_name": pa.array(["diagnosis"], type=pa.string()),
            "to_harmonize": pa.array(["lung cancer"], type=pa.string()),
            "top_harmonization": pa.array(["Lung Cancer"], type=pa.string()),
            "ontology_id": pa.array([123], type=pa.int64()),
            "top_harmonizations": pa.array([["Lung Cancer"]], type=pa.list_(pa.string())),
            "confidence_score": pa.array([0.75], type=pa.float32()),
            "error": pa.array([None], type=pa.string()),
            "row_indices": pa.array([[7]], type=pa.list_(pa.int64())),
        })
        pq.write_table(table, path)

        summary = read_manifest_parquet(path)

        assert summary is not None
        assert len(summary.rows) == 1
        assert summary.rows[0].column_id == 3
        assert summary.rows[0].ontology_id == "123"
        assert summary.rows[0].confidence_score == 0.75
        assert summary.rows[0].manual_overrides == []


class TestManifestToJson:
    """JSON conversion preserves all manifest data for human-readable download."""

    def test_manifest_to_json_preserves_nested_structures(self) -> None:
        """All fields including nested ManualOverride are serialized."""
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

    def test_manifest_to_json_handles_none_values(self) -> None:
        """Empty manual_overrides serialize correctly."""
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
        )

        with TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "test_manifest.parquet"
            _write_manifest_parquet(manifest_path, [row])

            json_str = _manifest_to_json(manifest_path)

            assert json_str is not None
            data = json.loads(json_str)
            parsed = data[0]

            assert parsed["manual_overrides"] == []
            assert parsed["ontology_id"] is None
