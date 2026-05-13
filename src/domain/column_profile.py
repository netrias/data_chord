"""Per-column distinct-value profiles for the Stage 2 takeover left pane.

Axis of change: how a column's data is summarized for the takeover. Owns the
``ColumnProfile`` dataclass (canonical "what's in this column" representation),
the API payload mirror, and the builders that tally values from iterables or
stored tabular files.

Invariant: ``sum(dv.count for dv in distinct_values) + null_count == total_rows``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from netrias_client import read_tabular
from pydantic import BaseModel

from src.domain.columns import ColumnKey, column_key_from_string


@dataclass(frozen=True)
class DistinctValue:
    value: str
    count: int


@dataclass(frozen=True, init=False)
class ColumnProfile:
    column_key: ColumnKey
    total_rows: int
    distinct_values: tuple[DistinctValue, ...]
    null_count: int

    def __init__(
        self,
        column_key: ColumnKey | str,
        total_rows: int,
        distinct_values: tuple[DistinctValue, ...],
        null_count: int,
    ) -> None:
        object.__setattr__(self, "column_key", column_key_from_string(str(column_key)))
        object.__setattr__(self, "total_rows", total_rows)
        object.__setattr__(self, "distinct_values", distinct_values)
        object.__setattr__(self, "null_count", null_count)

    @property
    def total_distinct(self) -> int:
        return len(self.distinct_values)

    @property
    def null_pct(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return round(self.null_count / self.total_rows * 100, 2)

    @property
    def is_all_unique(self) -> bool:
        # Empty columns are not "all unique" — there's nothing to be unique.
        return self.total_rows > 0 and self.total_distinct == self.total_rows


class DistinctValuePayload(BaseModel):
    value: str
    count: int


class ColumnProfilePayload(BaseModel):
    """Serialized column profile for API responses."""

    column_key: str
    total_rows: int
    distinct_values: list[DistinctValuePayload]
    null_count: int
    total_distinct: int
    null_pct: float
    is_all_unique: bool


def build_column_profile(column_key: ColumnKey | str, values: Iterable[str | None]) -> ColumnProfile:
    """Tally an iterable of values into a ``ColumnProfile``.

    Treats ``None`` and the empty string as null; everything else is counted
    by exact-string equality (per the project's whitespace-sensitive domain
    rules — see CLAUDE.md).
    """
    total_rows = 0
    null_count = 0
    counter: Counter[str] = Counter()

    for value in values:
        total_rows += 1
        if value is None or value == "":
            null_count += 1
            continue
        counter[value] += 1

    # most_common returns ties in insertion order, which is fine — the UI
    # only promises "sorted by count descending", not lexicographic ties.
    distinct_values = tuple(
        DistinctValue(value=v, count=c) for v, c in counter.most_common()
    )
    return ColumnProfile(
        column_key=column_key_from_string(str(column_key)),
        total_rows=total_rows,
        distinct_values=distinct_values,
        null_count=null_count,
    )


def build_column_profiles_from_tabular(
    tabular_path: Path,
    sheet_name: str | None = None,
) -> dict[str, ColumnProfile]:
    """Build profiles for every column in a stored tabular file."""
    dataset = read_tabular(tabular_path, sheet_name=sheet_name)
    return {
        column.key: build_column_profile(
            column.key,
            (row[column.index] if column.index < len(row) else "" for row in dataset.rows),
        )
        for column in dataset.columns
    }


def build_column_profile_from_tabular(
    tabular_path: Path,
    column_key: ColumnKey | str,
    sheet_name: str | None = None,
) -> ColumnProfile | None:
    """Build one column profile without requiring the analyze response cache."""
    dataset = read_tabular(tabular_path, sheet_name=sheet_name)
    column = next((candidate for candidate in dataset.columns if candidate.key == str(column_key)), None)
    if column is None:
        return None
    return build_column_profile(
        column.key,
        (row[column.index] if column.index < len(row) else "" for row in dataset.rows),
    )


def column_profile_to_payload(profile: ColumnProfile) -> ColumnProfilePayload:
    """Boundary conversion: domain dataclass to API model with derived fields."""
    return ColumnProfilePayload(
        column_key=str(profile.column_key),
        total_rows=profile.total_rows,
        distinct_values=[
            DistinctValuePayload(value=dv.value, count=dv.count)
            for dv in profile.distinct_values
        ],
        null_count=profile.null_count,
        total_distinct=profile.total_distinct,
        null_pct=profile.null_pct,
        is_all_unique=profile.is_all_unique,
    )
