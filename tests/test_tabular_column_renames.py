"""Tests for applying Stage 2 output names to tabular datasets."""

from __future__ import annotations

from netrias_client import TabularColumn, TabularFormat, dataset_from_rows

from src.domain.column_renames import ColumnRenameSet
from src.domain.tabular_column_renames import apply_column_renames_to_dataset, resolve_tabular_columns


def test_apply_column_renames_preserves_duplicate_header_identity() -> None:
    """Only the column with the matching stable key is renamed."""

    # Given: two source columns share the same visible header
    dataset = dataset_from_rows(
        columns=[
            TabularColumn(key="col_0000", index=0, header="diagnosis"),
            TabularColumn(key="col_0001", index=1, header="diagnosis"),
        ],
        rows=[["Lung", "Breast"]],
        source_format=TabularFormat.CSV,
    )
    assert dataset.headers == ["diagnosis", "diagnosis"]

    # When: Stage 2 renames only the second column by stable key
    renamed = apply_column_renames_to_dataset(
        dataset,
        ColumnRenameSet.from_dict({"col_0001": "Primary Diagnosis"}),
    )

    # Then: duplicate source headers remain distinct
    assert renamed.headers == ["diagnosis", "Primary Diagnosis"]
    assert renamed.rows == [["Lung", "Breast"]]


def test_apply_column_renames_allows_duplicate_output_names_for_distinct_columns() -> None:
    """Output names are column properties; duplicate labels do not collapse identity."""

    # Given: the source already has a blank standard column beside the populated source column
    dataset = dataset_from_rows(
        columns=[
            TabularColumn(key="col_0000", index=0, header="diagnosis"),
            TabularColumn(key="col_0001", index=1, header="disease_type"),
        ],
        rows=[["Lung", ""]],
        source_format=TabularFormat.CSV,
    )
    renames = ColumnRenameSet.from_dict({"col_0000": "disease_type"})

    # When: the app applies Stage 2 rename choices to the final output
    resolved = resolve_tabular_columns(dataset, renames)
    renamed = apply_column_renames_to_dataset(dataset, renames)

    # Then: table width and column identity are preserved, even if output names repeat
    assert [(column.key, column.original_name, column.output_name) for column in resolved] == [
        ("col_0000", "diagnosis", "disease_type"),
        ("col_0001", "disease_type", "disease_type"),
    ]
    assert renamed.headers == ["disease_type", "disease_type"]
    assert len(renamed.headers) == len(dataset.headers)
    assert len(set(renamed.headers)) == 1
    assert renamed.rows == [["Lung", ""]]
