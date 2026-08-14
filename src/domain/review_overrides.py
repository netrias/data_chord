"""Review override state stored between Stage 4 review and final export."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from netrias_client import TabularDataset

from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.manifest import ManifestManualOverride

REVIEW_OVERRIDES_SCHEMA_VERSION = 2


class InvalidReviewOverridesError(ValueError):
    """Raised when stored review state does not match the current contract."""


@dataclass(frozen=True)
class ReviewModeProgress:
    current_unit: int = 1
    batch_size: int = 5

    @classmethod
    def from_payload(cls, payload: object) -> ReviewModeProgress:
        if not isinstance(payload, Mapping):
            raise InvalidReviewOverridesError("Review mode progress must be an object.")
        if set(payload) != {"current_unit", "batch_size"}:
            raise InvalidReviewOverridesError("Review mode progress fields are invalid.")
        return cls(
            current_unit=_positive_int(payload.get("current_unit"), "current_unit"),
            batch_size=_positive_int(payload.get("batch_size"), "batch_size"),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "current_unit": self.current_unit,
            "batch_size": self.batch_size,
        }


@dataclass(frozen=True)
class ReviewProgressState:
    review_mode: str = "column"
    sort_mode: str = "original"
    scroll_mode: bool = False
    show_case_only_changes: bool = False
    show_unchanged_values: bool = False
    column_mode: ReviewModeProgress = ReviewModeProgress()
    row_mode: ReviewModeProgress = ReviewModeProgress()

    @classmethod
    def from_payload(cls, payload: object) -> ReviewProgressState:
        if not isinstance(payload, Mapping):
            raise InvalidReviewOverridesError("Review progress must be an object.")
        expected_fields = {
            "review_mode",
            "sort_mode",
            "scroll_mode",
            "show_case_only_changes",
            "show_unchanged_values",
            "column_mode",
            "row_mode",
        }
        if set(payload) != expected_fields:
            raise InvalidReviewOverridesError("Review progress fields are invalid.")

        review_mode = payload.get("review_mode")
        if not isinstance(review_mode, str) or review_mode not in {"column", "row"}:
            raise InvalidReviewOverridesError("Review mode is invalid.")
        sort_mode = payload.get("sort_mode")
        if not isinstance(sort_mode, str) or sort_mode not in {
            "original",
            "fidelity-asc",
            "fidelity-desc",
        }:
            raise InvalidReviewOverridesError("Review sort mode is invalid.")
        return cls(
            review_mode=review_mode,
            sort_mode=sort_mode,
            scroll_mode=_required_bool(payload.get("scroll_mode"), "scroll_mode"),
            show_case_only_changes=_required_bool(
                payload.get("show_case_only_changes"),
                "show_case_only_changes",
            ),
            show_unchanged_values=_required_bool(
                payload.get("show_unchanged_values"),
                "show_unchanged_values",
            ),
            column_mode=ReviewModeProgress.from_payload(payload.get("column_mode")),
            row_mode=ReviewModeProgress.from_payload(payload.get("row_mode")),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "review_mode": self.review_mode,
            "sort_mode": self.sort_mode,
            "scroll_mode": self.scroll_mode,
            "show_case_only_changes": self.show_case_only_changes,
            "show_unchanged_values": self.show_unchanged_values,
            "column_mode": self.column_mode.to_payload(),
            "row_mode": self.row_mode.to_payload(),
        }


@dataclass(frozen=True)
class CellOverride:
    human_value: str
    original_value: str | None

    @classmethod
    def from_payload(cls, payload: object) -> CellOverride:
        if not isinstance(payload, Mapping):
            raise InvalidReviewOverridesError("A cell override must be an object.")
        if set(payload) != {"human_value", "original_value"}:
            raise InvalidReviewOverridesError("Cell override fields are invalid.")
        human_value = payload.get("human_value")
        if not isinstance(human_value, str):
            raise InvalidReviewOverridesError("A cell override human value must be text.")
        original_value = payload.get("original_value")
        if original_value is not None and not isinstance(original_value, str):
            raise InvalidReviewOverridesError("A cell override original value must be text or null.")
        return cls(
            human_value=human_value,
            original_value=original_value,
        )

    def to_payload(self) -> dict[str, str | None]:
        return {
            "human_value": self.human_value,
            "original_value": self.original_value,
        }


@dataclass(frozen=True)
class ReviewOverrides:
    file_id: str
    created_at: datetime
    updated_at: datetime
    overrides: dict[str, dict[ColumnKey, CellOverride]]
    review_state: ReviewProgressState

    @classmethod
    def create(
        cls,
        *,
        file_id: str,
        overrides: object,
        review_state: ReviewProgressState,
        created_at: datetime,
        updated_at: datetime,
    ) -> ReviewOverrides:
        return cls(
            file_id=file_id,
            created_at=created_at,
            updated_at=updated_at,
            overrides=_parse_overrides(overrides),
            review_state=review_state,
        )

    @classmethod
    def from_store(cls, payload: object, expected_file_id: str) -> ReviewOverrides:
        if not isinstance(payload, Mapping):
            raise InvalidReviewOverridesError("Stored review overrides must be an object.")
        expected_fields = {
            "schema_version",
            "file_id",
            "created_at",
            "updated_at",
            "overrides",
            "review_state",
        }
        if set(payload) != expected_fields:
            raise InvalidReviewOverridesError("Stored review override fields are invalid.")
        schema_version = payload.get("schema_version")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != REVIEW_OVERRIDES_SCHEMA_VERSION
        ):
            raise InvalidReviewOverridesError("Stored review override schema version is invalid.")
        file_id = payload.get("file_id")
        if file_id != expected_file_id:
            raise InvalidReviewOverridesError("Stored review override file identity is invalid.")
        created_at = _datetime_from_payload(payload.get("created_at"))
        updated_at = _datetime_from_payload(payload.get("updated_at"))
        if created_at > updated_at:
            raise InvalidReviewOverridesError("Stored review override timestamps are out of order.")
        return cls(
            file_id=expected_file_id,
            created_at=created_at,
            updated_at=updated_at,
            overrides=_parse_overrides(payload.get("overrides")),
            review_state=ReviewProgressState.from_payload(payload.get("review_state")),
        )

    def to_store(self) -> dict[str, object]:
        return {
            "schema_version": REVIEW_OVERRIDES_SCHEMA_VERSION,
            "file_id": self.file_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "overrides": {
                row_key: {str(column_key): override.to_payload() for column_key, override in columns.items()}
                for row_key, columns in self.overrides.items()
            },
            "review_state": self.review_state.to_payload(),
        }

    def human_values_by_row(self) -> dict[str, dict[ColumnKey, str]]:
        return {
            row_key: {
                column_key: override.human_value
                for column_key, override in columns.items()
            }
            for row_key, columns in self.overrides.items()
        }

    def manual_override_batch(self) -> list[ManifestManualOverride]:
        """Deduplicate by (column, original, value) before writing manifest audit rows."""
        seen: set[tuple[str, str, str]] = set()
        batch: list[ManifestManualOverride] = []
        for columns in self.overrides.values():
            for column_key, override in columns.items():
                if override.original_value is None:
                    continue
                key = (str(column_key), override.original_value, override.human_value)
                if key in seen:
                    continue
                seen.add(key)
                batch.append(
                    ManifestManualOverride.from_raw(column_key, override.original_value, override.human_value)
                )
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
        raise InvalidReviewOverridesError("Review overrides must be an object.")
    parsed: dict[str, dict[ColumnKey, CellOverride]] = {}
    for raw_row_key, raw_columns in payload.items():
        if not isinstance(raw_row_key, str) or not isinstance(raw_columns, Mapping):
            raise InvalidReviewOverridesError("Review override row fields are invalid.")
        if not raw_row_key.isdecimal() or int(raw_row_key) < 1:
            raise InvalidReviewOverridesError("Review override row identity is invalid.")
        parsed[raw_row_key] = _parse_row_overrides(raw_columns)
    return parsed


def _parse_row_overrides(payload: Mapping[object, object]) -> dict[ColumnKey, CellOverride]:
    parsed: dict[ColumnKey, CellOverride] = {}
    for raw_column_key, raw_override in payload.items():
        if not isinstance(raw_column_key, str) or not raw_column_key:
            raise InvalidReviewOverridesError("Review override column identity is invalid.")
        override = CellOverride.from_payload(raw_override)
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


def _required_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidReviewOverridesError(f"Review progress {field} must be true or false.")
    return value


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidReviewOverridesError(f"Review progress {field} must be a positive integer.")
    return value


def _datetime_from_payload(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidReviewOverridesError("Review override timestamps must be text.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InvalidReviewOverridesError("Review override timestamps must be valid ISO timestamps.") from None
    if parsed.utcoffset() is None:
        raise InvalidReviewOverridesError("Review override timestamps must include a time zone.")
    return parsed


__all__ = [
    "CellOverride",
    "InvalidReviewOverridesError",
    "REVIEW_OVERRIDES_SCHEMA_VERSION",
    "ReviewModeProgress",
    "ReviewOverrides",
    "ReviewProgressState",
]
