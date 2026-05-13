"""User-selected output column names keyed by source column identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.domain.columns import ColumnKey, column_key_from_string


@dataclass(frozen=True)
class ColumnRenameSet:
    renames: dict[ColumnKey, str]

    @classmethod
    def empty(cls) -> ColumnRenameSet:
        return cls(renames={})

    @classmethod
    def from_dict(cls, renames: Mapping[str, str]) -> ColumnRenameSet:
        return cls(
            renames={
                column_key_from_string(column_key): cleaned_name
                for column_key, column_name in renames.items()
                if (cleaned_name := column_name.strip())
            }
        )

    def to_strings(self) -> dict[str, str]:
        return {str(column_key): column_name for column_key, column_name in self.renames.items()}


__all__ = ["ColumnRenameSet"]
