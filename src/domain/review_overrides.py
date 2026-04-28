"""Review override state stored between Stage 4 review and final export."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from netrias_client import TabularDataset

from src.domain.columns import ColumnKey, column_key_from_string


@dataclass(frozen=True)
class CellOverride:
    ai_value: str | None
    human_value: str
    original_value: str | None

    @classmethod
    def from_payload(cls, payload: object) -> CellOverride | None:
        if not isinstance(payload, Mapping):
            return None
        human_value = payload.get("human_value")
        if not isinstance(human_value, str):
            return None
        return cls(
            ai_value=_optional_string(payload.get("ai_value")),
            human_value=human_value,
            original_value=_optional_string(payload.get("original_value")),
        )

    def to_payload(self) -> dict[str, str | None]:
        return {
            "ai_value": self.ai_value,
            "human_value": self.human_value,
            "original_value": self.original_value,
        }


@dataclass(frozen=True)
class ReviewOverrides:
    file_id: str
    created_at: datetime
    updated_at: datetime
    overrides: dict[str, dict[ColumnKey, CellOverride]]
    review_state: dict[str, object]

    @classmethod
    def create(
        cls,
        *,
        file_id: str,
        overrides: object,
        review_state: Mapping[str, object],
        created_at: datetime,
        updated_at: datetime,
    ) -> ReviewOverrides:
        return cls(
            file_id=file_id,
            created_at=created_at,
            updated_at=updated_at,
            overrides=_parse_overrides(overrides),
            review_state=dict(review_state),
        )

    @classmethod
    def from_store(cls, payload: object, fallback_file_id: str) -> ReviewOverrides | None:
        if not isinstance(payload, Mapping):
            return None
        file_id = payload.get("file_id")
        created_at = _datetime_from_payload(payload.get("created_at"))
        updated_at = _datetime_from_payload(payload.get("updated_at"))
        overrides = payload.get("overrides")
        review_state = payload.get("review_state")
        return cls(
            file_id=file_id if isinstance(file_id, str) else fallback_file_id,
            created_at=created_at,
            updated_at=updated_at,
            overrides=_parse_overrides(overrides if isinstance(overrides, Mapping) else {}),
            review_state=dict(review_state) if isinstance(review_state, Mapping) else {},
        )

    def to_store(self) -> dict[str, object]:
        return {
            "file_id": self.file_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "overrides": {
                row_key: {str(column_key): override.to_payload() for column_key, override in columns.items()}
                for row_key, columns in self.overrides.items()
            },
            "review_state": self.review_state,
        }

    def human_values_by_row(self) -> dict[str, dict[ColumnKey, str]]:
        return {
            row_key: {
                column_key: override.human_value
                for column_key, override in columns.items()
            }
            for row_key, columns in self.overrides.items()
        }

    def manual_override_batch(self) -> list[tuple[str, str, str]]:
        """Deduplicate by (column, original, value) before writing manifest audit rows."""
        seen: set[tuple[str, str, str]] = set()
        batch: list[tuple[str, str, str]] = []
        for columns in self.overrides.values():
            for column_key, override in columns.items():
                if override.original_value is None:
                    continue
                key = (str(column_key), override.original_value, override.human_value)
                if key in seen:
                    continue
                seen.add(key)
                batch.append(key)
        return batch

    def apply_to_rows(self, rows: list[list[str]], dataset: TabularDataset) -> list[list[str]]:
        """Row keys are 1-indexed to match Stage 4 UI numbering."""
        row_overrides = self.human_values_by_row()
        if not row_overrides:
            return rows
        column_lookup = {column.key: column.index for column in dataset.columns}
        return [
            _apply_row_override(row, row_overrides.get(str(index + 1), {}), column_lookup)
            for index, row in enumerate(rows)
        ]


def _parse_overrides(payload: object) -> dict[str, dict[ColumnKey, CellOverride]]:
    if not isinstance(payload, Mapping):
        return {}
    parsed: dict[str, dict[ColumnKey, CellOverride]] = {}
    for raw_row_key, raw_columns in payload.items():
        if not isinstance(raw_row_key, str) or not isinstance(raw_columns, Mapping):
            continue
        parsed[raw_row_key] = _parse_row_overrides(raw_columns)
    return parsed


def _parse_row_overrides(payload: Mapping[object, object]) -> dict[ColumnKey, CellOverride]:
    parsed: dict[ColumnKey, CellOverride] = {}
    for raw_column_key, raw_override in payload.items():
        if not isinstance(raw_column_key, str):
            continue
        override = CellOverride.from_payload(raw_override)
        if override is not None:
            parsed[column_key_from_string(raw_column_key)] = override
    return parsed


def _apply_row_override(
    row: list[str],
    row_overrides: Mapping[ColumnKey, str],
    column_lookup: Mapping[str, int],
) -> list[str]:
    if not row_overrides:
        return row
    result = list(row)
    for column_key, value in row_overrides.items():
        index = column_lookup.get(str(column_key))
        if index is not None and index < len(result):
            result[index] = value
    return result


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _datetime_from_payload(value: object) -> datetime:
    if not isinstance(value, str):
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


__all__ = [
    "CellOverride",
    "ReviewOverrides",
]
