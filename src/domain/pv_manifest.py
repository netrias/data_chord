"""PV manifest persisted for cache recovery after server restarts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.domain.column_cde_map import ColumnCdeMap


@dataclass(frozen=True)
class PVManifest:
    data_model_key: str
    version_label: str
    column_to_cde_key: ColumnCdeMap
    pvs: dict[str, frozenset[str]]

    @classmethod
    def from_store(cls, payload: object) -> PVManifest | None:
        if not isinstance(payload, Mapping):
            return None
        raw_mappings = payload.get("column_to_cde_key")
        raw_pvs = payload.get("pvs")
        return cls(
            data_model_key=_string_or_empty(payload.get("data_model_key")),
            version_label=_string_or_empty(payload.get("version_label")),
            column_to_cde_key=ColumnCdeMap.from_strings(_string_mapping(raw_mappings)),
            pvs=_pv_mapping(raw_pvs),
        )

    def to_store(self) -> dict[str, object]:
        return {
            "data_model_key": self.data_model_key,
            "version_label": self.version_label,
            "column_to_cde_key": self.column_to_cde_key.to_strings(),
            "pvs": {cde_key: sorted(values) for cde_key, values in self.pvs.items()},
        }


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: mapped
        for key, mapped in value.items()
        if isinstance(key, str) and isinstance(mapped, str)
    }


def _pv_mapping(value: object) -> dict[str, frozenset[str]]:
    if not isinstance(value, Mapping):
        return {}
    parsed: dict[str, frozenset[str]] = {}
    for cde_key, raw_values in value.items():
        if isinstance(cde_key, str) and isinstance(raw_values, list):
            parsed[cde_key] = frozenset(item for item in raw_values if isinstance(item, str))
    return parsed


__all__ = ["PVManifest"]
