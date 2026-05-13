"""Column-to-CDE relationships used for PV lookup and cache persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.domain.cde import normalize_cde_key
from src.domain.columns import ColumnKey, column_key_from_string


@dataclass(frozen=True)
class ColumnCdeMap:
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

    def with_overrides(self, overrides: Mapping[ColumnKey, str | None]) -> ColumnCdeMap:
        merged = dict(self.mappings)
        for column_key, cde_key in overrides.items():
            if cde_key is None:
                merged.pop(column_key, None)
            else:
                merged[column_key] = cde_key
        return ColumnCdeMap(merged)

    def cde_keys(self) -> list[str]:
        return sorted(set(self.mappings.values()))

    def to_strings(self) -> dict[str, str]:
        return {str(column_key): cde_key for column_key, cde_key in self.mappings.items()}


__all__ = ["ColumnCdeMap"]
