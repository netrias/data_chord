"""Catalog views for CDE metadata keyed by stable CDE key."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.domain.cde import CDEInfo


@dataclass(frozen=True)
class CdeCatalog:
    """CDE metadata with keyed lookup kept behind a domain API."""

    cdes: tuple[CDEInfo, ...] = ()
    _by_key: Mapping[str, CDEInfo] = MappingProxyType({})

    @classmethod
    def from_cdes(cls, cdes: Iterable[CDEInfo]) -> CdeCatalog:
        cde_tuple = tuple(cdes)
        return cls(cdes=cde_tuple, _by_key=MappingProxyType({cde.cde_key: cde for cde in cde_tuple}))

    @classmethod
    def empty(cls) -> CdeCatalog:
        return cls()

    def __iter__(self) -> Iterator[CDEInfo]:
        return iter(self.cdes)

    def __len__(self) -> int:
        return len(self.cdes)

    def is_empty(self) -> bool:
        return not self.cdes

    def get(self, cde_key: str) -> CDEInfo | None:
        return self._by_key.get(cde_key)

    def keys(self) -> tuple[str, ...]:
        return tuple(cde.cde_key for cde in self.cdes)

    def to_list(self) -> list[CDEInfo]:
        return list(self.cdes)

    def cde_types_payload(self) -> dict[str, str]:
        return {cde.cde_key: cde.cde_type.value for cde in self.cdes}


__all__ = ["CdeCatalog"]
