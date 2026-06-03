"""Apply confirmed output column names to tabular datasets."""

from __future__ import annotations

from dataclasses import dataclass, replace

from netrias_client import TabularColumn, TabularDataset, dataset_from_rows

from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import ColumnKey, column_key_from_string


@dataclass(frozen=True)
class ResolvedTabularColumn:
    """Column identity plus the output name selected for later stages."""

    key: ColumnKey
    index: int
    original_name: str
    output_name: str


def resolve_tabular_columns(dataset: TabularDataset, renames: ColumnRenameSet) -> tuple[ResolvedTabularColumn, ...]:
    """Resolve every column to an output name, falling back to the source header."""
    return tuple(_resolve_tabular_column(column, renames) for column in dataset.columns)


def apply_column_renames_to_dataset(dataset: TabularDataset, renames: ColumnRenameSet) -> TabularDataset:
    """Return a dataset with Stage 2 output headers applied by stable column key."""
    resolved_by_key = {column.key: column for column in resolve_tabular_columns(dataset, renames)}
    renamed_columns = [
        replace(column, header=resolved_by_key[column_key_from_string(column.key)].output_name)
        for column in dataset.columns
    ]
    return dataset_from_rows(
        columns=renamed_columns,
        rows=dataset.rows,
        source_format=dataset.source_format,
        sheet_name=dataset.sheet_name,
    )


def _resolve_tabular_column(column: TabularColumn, renames: ColumnRenameSet) -> ResolvedTabularColumn:
    column_key = column_key_from_string(column.key)
    return ResolvedTabularColumn(
        key=column_key,
        index=column.index,
        original_name=column.header,
        output_name=renames.renames.get(column_key, column.header),
    )


__all__ = [
    "ResolvedTabularColumn",
    "apply_column_renames_to_dataset",
    "resolve_tabular_columns",
]
