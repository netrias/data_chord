"""Named manifest update requests keyed by source term identity."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.manifest.models import ManifestRow


@dataclass(frozen=True, order=True)
class ManifestTermKey:
    """Identity for one source term within a manifest column."""

    column_key: ColumnKey
    original_value: str

    @classmethod
    def from_raw(cls, column_key: ColumnKey | str, original_value: str) -> ManifestTermKey:
        return cls(column_key=column_key_from_string(str(column_key)), original_value=original_value)

    @classmethod
    def from_row(cls, row: ManifestRow) -> ManifestTermKey:
        return cls(column_key=row.column_key, original_value=row.to_harmonize)


@dataclass(frozen=True)
class ManifestPvAdjustment:
    """PV-driven replacement for a non-conformant harmonized value."""

    term_key: ManifestTermKey
    adjusted_value: str

    @classmethod
    def from_raw(
        cls,
        column_key: ColumnKey | str,
        original_value: str,
        adjusted_value: str,
    ) -> ManifestPvAdjustment:
        return cls(
            term_key=ManifestTermKey.from_raw(column_key, original_value),
            adjusted_value=adjusted_value,
        )


__all__ = ["ManifestPvAdjustment", "ManifestTermKey"]
