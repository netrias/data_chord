"""Permissible value sets keyed by CDE key."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.domain.cde_catalog import CdeCatalog


@dataclass(frozen=True)
class CdePvCatalog:
    """PV sets for a data model version, keyed by CDE key."""

    values: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def __contains__(self, cde_key: object) -> bool:
        return isinstance(cde_key, str) and cde_key in self.values

    def __len__(self) -> int:
        return len(self.values)

    @classmethod
    def empty(cls) -> CdePvCatalog:
        return cls({})

    @classmethod
    def from_mapping(cls, values: Mapping[str, frozenset[str]]) -> CdePvCatalog:
        return cls(values)

    def get(self, cde_key: str) -> frozenset[str] | None:
        return self.values.get(cde_key)

    def has(self, cde_key: str) -> bool:
        return cde_key in self.values

    def has_any(self) -> bool:
        return bool(self.values)

    def with_values(self, values: Mapping[str, frozenset[str]]) -> CdePvCatalog:
        merged = dict(self.values)
        merged.update(values)
        return CdePvCatalog(merged)

    def with_defaults(self, cde_keys: Iterable[str]) -> CdePvCatalog:
        return self.with_values({cde_key: self.get(cde_key) or frozenset() for cde_key in cde_keys})

    def missing_for(self, catalog: CdeCatalog) -> list[str]:
        return [cde.cde_key for cde in catalog if cde.cde_key not in self.values]

    def counts(self) -> dict[str, int]:
        return {cde_key: len(values) for cde_key, values in self.values.items()}

    def total_count(self) -> int:
        return sum(len(values) for values in self.values.values())

    def to_mapping(self) -> dict[str, frozenset[str]]:
        return dict(self.values)


__all__ = ["CdePvCatalog"]
