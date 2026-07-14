"""Stage 1 analyze prepares profiles without bloating the analyze response."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.domain.cde import CDEInfo, CdeType, ModelSuggestion
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_profile import ColumnProfile, DistinctValue
from src.domain.manifest import ColumnMappingManifest
from src.stage_1_upload.router import _build_column_summaries
from src.stage_1_upload.services import analyze_columns


@pytest.fixture
def two_column_csv(tmp_path: Path) -> Path:
    """Two columns: one with repeats, one with empty values."""
    csv_path = tmp_path / "data.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["diagnosis", "age"])
        writer.writerow(["Lung Cancer", "57"])
        writer.writerow(["Lung Cancer", "62"])
        writer.writerow(["Breast Cancer", ""])
        writer.writerow(["Lung Cancer", "29"])
    return csv_path


def test_analyze_returns_profiles_for_server_cache(two_column_csv: Path) -> None:
    """
    Given: a 4-row CSV with two columns
    When: analyze_columns is called
    Then: the returned profile dict has one entry per column with totals matching
          the input rows, distinct values sorted by count desc, and null_count
          covering empty cells
    """
    # Given: csv created in fixture; nothing analyzed yet
    profiles: dict | None = None
    assert profiles is None

    # When
    total_rows, columns, profiles = analyze_columns(two_column_csv)

    # Then: profile keys match the column keys
    assert total_rows == 4
    assert profiles is not None
    assert set(profiles.keys()) == {c.column_key for c in columns}

    diagnosis = profiles[columns[0].column_key]
    age = profiles[columns[1].column_key]

    assert diagnosis.total_rows == 4
    assert diagnosis.distinct_values[0].value == "Lung Cancer"
    assert diagnosis.distinct_values[0].count == 3
    assert diagnosis.null_count == 0

    assert age.total_rows == 4
    assert age.null_count == 1  # one empty cell
    assert age.total_distinct == 3  # 57, 62, 29


def test_column_preview_empty_visibility_uses_full_profile(tmp_path: Path) -> None:
    """
    Given: a column whose first five preview rows are empty but later rows have data
    When: analyze_columns builds the lightweight Stage 1 column preview
    Then: has_non_empty_values reflects the full file, not only the preview sample.
    """
    csv_path = tmp_path / "late-value.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["late_value", "all_blank"])
        for _ in range(5):
            writer.writerow(["", ""])
        writer.writerow(["present", ""])

    total_rows, columns, profiles = analyze_columns(csv_path)

    assert total_rows == 6
    late_value = columns[0]
    all_blank = columns[1]
    assert late_value.has_non_empty_values is True
    assert profiles[late_value.column_key].total_distinct == 1
    assert all_blank.has_non_empty_values is False


def test_analyze_response_does_not_require_full_profiles() -> None:
    """
    Given: the analyze API response schema
    When: a response is built without the optional profile map
    Then: the profile map defaults to empty so the browser does not need to
          store every distinct value for every column
    """
    from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
    from src.domain.manifest import ConfidenceBucket
    from src.stage_1_upload.schemas import AnalyzeResponse, ColumnSummary

    # Given: no profile payload has been supplied
    profile_payload = None
    assert profile_payload is None

    # When
    response = AnalyzeResponse(
        file_id=dataset_workflow_id_from_string("abcdef0123456789abcdef0123456789"),
        file_name="data.csv",
        external_version_number="11.0.4",
        total_rows=1,
        columns=[
            ColumnSummary(
                column_name="diagnosis",
                column_key="diagnosis",
                source_index=0,
                header="diagnosis",
                inferred_type="text",
                has_non_empty_values=True,
                confidence_bucket=ConfidenceBucket.HIGH,
                confidence_score=1.0,
            )
        ],
        cde_targets={},
        next_stage="mapping",
        next_step_hint="Review mappings.",
    )

    # Then
    assert response.column_profiles == {}


def test_analyze_marks_columns_with_late_values_as_non_empty(tmp_path: Path) -> None:
    """
    Given: one sparse column with later values and one fully blank column
    When: analyze_columns builds the Stage 1 response contract
    Then: the compact value-presence flag reflects the full dataset
    """
    # Given
    csv_path = tmp_path / "late-values.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["late_value", "all_blank"])
        for _ in range(5):
            writer.writerow(["", ""])
        writer.writerow(["present", ""])

    # When
    _, columns, profiles = analyze_columns(csv_path)

    # Then
    by_name = {column.column_name: column for column in columns}
    assert by_name["late_value"].has_non_empty_values is True
    assert by_name["all_blank"].has_non_empty_values is False
    assert profiles[by_name["late_value"].column_key].total_distinct == 1


def test_build_column_summaries_reports_ai_rec_overlap_ratios() -> None:
    """
    Given: mixed AI recommendations and a warmed CDE/PV catalog
    When: Stage 1 builds column summaries for the analyze response
    Then: only PV recommendations with distinct values receive an overlap ratio
    """
    # Given
    profiles = {
        "diagnosis": ColumnProfile(
            column_key="diagnosis",
            total_rows=5,
            distinct_values=(
                DistinctValue("Lung", 1),
                DistinctValue("Breast", 1),
                DistinctValue("Glioma", 1),
                DistinctValue("Other", 1),
                DistinctValue("Unknown", 1),
            ),
            null_count=0,
        ),
        "notes": ColumnProfile(
            column_key="notes",
            total_rows=1,
            distinct_values=(DistinctValue("free text", 1),),
            null_count=0,
        ),
        "age_value": ColumnProfile(
            column_key="age_value",
            total_rows=1,
            distinct_values=(DistinctValue("57", 1),),
            null_count=0,
        ),
        "unknown_field": ColumnProfile(
            column_key="unknown_field",
            total_rows=1,
            distinct_values=(DistinctValue("x", 1),),
            null_count=0,
        ),
        "fallback_field": ColumnProfile(
            column_key="fallback_field",
            total_rows=2,
            distinct_values=(DistinctValue("Lung", 1), DistinctValue("Unknown", 1)),
            null_count=0,
        ),
        "empty_col": ColumnProfile(
            column_key="empty_col",
            total_rows=1,
            distinct_values=(),
            null_count=1,
        ),
    }
    cdes = [
        CDEInfo(cde_id=1, cde_key="dx", description=None, cde_type=CdeType.PV),
        CDEInfo(cde_id=2, cde_key="notes_cde", description=None, cde_type=CdeType.PASSTHROUGH),
        CDEInfo(cde_id=3, cde_key="age_cde", description=None, cde_type=CdeType.PASSTHROUGH),
        CDEInfo(cde_id=4, cde_key="empty_dx", description=None, cde_type=CdeType.PV),
    ]
    pv_sets = {
        "dx": frozenset({"Lung", "Breast", "Glioma", "Other"}),
        "empty_dx": frozenset({"Present"}),
    }
    cde_targets = {
        "diagnosis": [ModelSuggestion(target="dx", similarity=0.95)],
        "notes": [ModelSuggestion(target="notes_cde", similarity=0.9)],
        "age_value": [ModelSuggestion(target="age_cde", similarity=0.9)],
        "unknown_field": [ModelSuggestion(target="not_in_catalog", similarity=0.8)],
        "fallback_field": [
            ModelSuggestion(target="not_in_catalog", similarity=0.9),
            ModelSuggestion(target="dx", similarity=0.8),
        ],
        "empty_col": [ModelSuggestion(target="empty_dx", similarity=0.7)],
    }
    summaries = {}
    assert summaries == {}

    # When
    mapping_manifest = ColumnMappingManifest.from_payload({
        "column_mappings": {
            column_key: {
                "cde_key": suggestions[0].target,
                "cde_id": index,
                "alternatives": [
                    {"target": suggestion.target, "confidence": suggestion.similarity}
                    for suggestion in suggestions
                ],
            }
            for index, (column_key, suggestions) in enumerate(cde_targets.items(), start=1)
        }
    })
    summaries = _build_column_summaries(
        profiles,
        mapping_manifest,
        CdeCatalog.from_cdes(cdes),
        CdePvCatalog.from_mapping(pv_sets),
    )

    # Then
    assert summaries["diagnosis"].value_overlap_ratio == 0.8
    assert summaries["notes"].value_overlap_ratio is None
    assert summaries["age_value"].value_overlap_ratio is None
    assert summaries["unknown_field"].value_overlap_ratio is None
    assert summaries["fallback_field"].value_overlap_ratio == 0.5
    assert summaries["empty_col"].value_overlap_ratio is None
