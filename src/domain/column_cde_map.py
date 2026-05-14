"""Column-to-CDE relationships used for PV lookup and cache persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.domain.cde import normalize_cde_key
from src.domain.columns import ColumnKey, column_key_from_string


@dataclass(frozen=True)
class ColumnCdeMap:
    """Immutable source-column to selected-CDE mapping.

    This is the value object passed between Stage 2 selection, Stage 3 PV
    fetching, and cache persistence. The keys are stable ``ColumnKey`` values,
    not display headers, so duplicate source headers stay distinct.
    """

    mappings: Mapping[ColumnKey, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "mappings", MappingProxyType(dict(self.mappings)))

    @classmethod
    def empty(cls) -> ColumnCdeMap:
        return cls(mappings={})

    @classmethod
    def from_strings(cls, mappings: Mapping[str, str | None]) -> ColumnCdeMap:
        return cls(
            mappings={
                column_key_from_string(column_key): normalized_cde_key
                for column_key, cde_key in mappings.items()
                if (normalized_cde_key := normalize_cde_key(cde_key)) is not None
            }
        )

    def with_overrides(self, overrides: ColumnCdeOverrides) -> ColumnCdeMap:
        merged = dict(self.mappings)
        for column_key, cde_key in overrides.overrides.items():
            if cde_key is None:
                merged.pop(column_key, None)
            else:
                merged[column_key] = cde_key
        return ColumnCdeMap(merged)

    def cde_keys(self) -> list[str]:
        return sorted(set(self.mappings.values()))

    def to_strings(self) -> dict[str, str]:
        return {str(column_key): cde_key for column_key, cde_key in self.mappings.items()}


@dataclass(frozen=True)
class ColumnCdeOverrides:
    """User edits to a column-to-CDE map.

    This is the canonical representation for Stage 2 manual mapping choices.
    A string value means the user selected that CDE. ``None`` means the user
    explicitly selected "No Mapping", so downstream code should remove any AI
    mapping for that column.
    """

    overrides: Mapping[ColumnKey, str | None]

    def __post_init__(self) -> None:
        object.__setattr__(self, "overrides", MappingProxyType(dict(self.overrides)))

    @classmethod
    def empty(cls) -> ColumnCdeOverrides:
        return cls(overrides={})

    @classmethod
    def from_strings(cls, overrides: Mapping[str, str | None]) -> ColumnCdeOverrides:
        return cls(
            overrides={
                column_key_from_string(column_key): normalize_cde_key(selection)
                for column_key, selection in overrides.items()
            }
        )

    @property
    def is_empty(self) -> bool:
        return not self.overrides

    def applied_items(self) -> list[tuple[ColumnKey, str]]:
        return [(column_key, cde_key) for column_key, cde_key in self.overrides.items() if cde_key is not None]

    def skipped_columns(self) -> list[ColumnKey]:
        return [column_key for column_key, cde_key in self.overrides.items() if cde_key is None]

    def to_strings(self) -> dict[str, str | None]:
        return {str(column_key): cde_key for column_key, cde_key in self.overrides.items()}


__all__ = ["ColumnCdeMap", "ColumnCdeOverrides"]
