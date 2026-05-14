"""Stable tabular column identity shared across review, manifest, and cache logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from netrias_client import column_key_for_index as _sdk_column_key_for_index

ColumnKey = NewType("ColumnKey", str)


def column_key_for_index(index: int) -> ColumnKey:
    return ColumnKey(_sdk_column_key_for_index(index))


def column_key_from_string(value: str) -> ColumnKey:
    return ColumnKey(value)


@dataclass(frozen=True)
class ColumnIdentity:
    key: ColumnKey
    index: int
    header: str

    @property
    def label(self) -> str:
        return self.header or "Unknown"


__all__ = [
    "ColumnIdentity",
    "ColumnKey",
    "column_key_for_index",
    "column_key_from_string",
]
