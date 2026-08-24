"""Review override state stored between Stage 4 review and final export."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cached_property
from types import MappingProxyType

from netrias_client import TabularDataset

from src.domain.columns import ColumnKey, column_key_from_string

REVIEW_OVERRIDES_SCHEMA_VERSION = 3


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
    original_value: str

    @classmethod
    def from_payload(cls, payload: object) -> CellOverride:
        if not isinstance(payload, Mapping):
            raise InvalidReviewOverridesError("A cell override must be an object.")
        if set(payload) != {"human_value", "original_value"}:
            raise InvalidReviewOverridesError("Cell override fields are invalid.")
        human_value = payload.get("human_value")
        if not isinstance(human_value, str) or not human_value:
            raise InvalidReviewOverridesError("A cell override human value must be non-empty text.")
        original_value = payload.get("original_value")
        if not isinstance(original_value, str):
            raise InvalidReviewOverridesError("A cell override original value must be text.")
        return cls(
            human_value=human_value,
            original_value=original_value,
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "human_value": self.human_value,
            "original_value": self.original_value,
        }


class ReviewOverrideAction(StrEnum):
    SET = "set"
    CLEAR = "clear"


@dataclass(frozen=True)
class ReviewOverrideEvent:
    """One immutable reviewer decision for one source cell."""

    kind: ReviewOverrideAction
    row_key: str
    column_key: ColumnKey
    original_value: str
    selected_value: str | None
    timestamp: datetime

    @classmethod
    def from_payload(cls, payload: object) -> ReviewOverrideEvent:
        if not isinstance(payload, Mapping):
            raise InvalidReviewOverridesError("A review event must be an object.")
        expected_fields = {
            "kind",
            "row_key",
            "column_key",
            "original_value",
            "selected_value",
            "timestamp",
        }
        if set(payload) != expected_fields:
            raise InvalidReviewOverridesError("Review event fields are invalid.")
        row_key = _row_key_from_payload(payload.get("row_key"))
        raw_column_key = payload.get("column_key")
        if not isinstance(raw_column_key, str) or not raw_column_key:
            raise InvalidReviewOverridesError("Review event column identity is invalid.")
        original_value = payload.get("original_value")
        if not isinstance(original_value, str):
            raise InvalidReviewOverridesError("Review event original value must be text.")
        raw_kind = payload.get("kind")
        try:
            kind = ReviewOverrideAction(raw_kind)
        except (TypeError, ValueError):
            raise InvalidReviewOverridesError("Review event action is invalid.") from None
        selected_value = payload.get("selected_value")
        if kind is ReviewOverrideAction.SET and (
            not isinstance(selected_value, str) or not selected_value
        ):
            raise InvalidReviewOverridesError("A set event selected value must be non-empty text.")
        if kind is ReviewOverrideAction.CLEAR and selected_value is not None:
            raise InvalidReviewOverridesError("A clear event selected value must be null.")
        return cls(
            kind=kind,
            row_key=row_key,
            column_key=column_key_from_string(raw_column_key),
            original_value=original_value,
            selected_value=selected_value,
            timestamp=_datetime_from_payload(payload.get("timestamp")),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "row_key": self.row_key,
            "column_key": str(self.column_key),
            "original_value": self.original_value,
            "selected_value": self.selected_value,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class ReviewOverrides:
    file_id: str
    created_at: datetime
    updated_at: datetime
    events: tuple[ReviewOverrideEvent, ...]
    review_state: ReviewProgressState

    @classmethod
    def from_snapshot(
        cls,
        *,
        file_id: str,
        overrides: object,
        review_state: ReviewProgressState,
        created_at: datetime,
        updated_at: datetime,
    ) -> ReviewOverrides:
        parsed = _parse_overrides(overrides)
        events = tuple(
            ReviewOverrideEvent(
                kind=ReviewOverrideAction.SET,
                row_key=row_key,
                column_key=column_key,
                original_value=override.original_value,
                selected_value=override.human_value,
                timestamp=updated_at,
            )
            for row_key, columns in parsed.items()
            for column_key, override in columns.items()
        )
        return cls(
            file_id=file_id,
            created_at=created_at,
            updated_at=updated_at,
            events=events,
            review_state=review_state,
        )

    def transition_to_snapshot(
        self,
        *,
        overrides: object,
        review_state: ReviewProgressState,
        updated_at: datetime,
    ) -> ReviewOverrides:
        """Append only decisions needed to reach one complete active snapshot."""
        if updated_at < self.updated_at:
            raise InvalidReviewOverridesError("Review override updates must be chronological.")
        requested = _parse_overrides(overrides)
        active = self.overrides
        identities = {
            (row_key, column_key)
            for row_key, columns in active.items()
            for column_key in columns
        } | {
            (row_key, column_key)
            for row_key, columns in requested.items()
            for column_key in columns
        }
        new_events: list[ReviewOverrideEvent] = []
        for row_key, column_key in sorted(identities, key=lambda item: (int(item[0]), str(item[1]))):
            current = active.get(row_key, {}).get(column_key)
            selected = requested.get(row_key, {}).get(column_key)
            if selected is None:
                if current is not None:
                    new_events.append(ReviewOverrideEvent(
                        kind=ReviewOverrideAction.CLEAR,
                        row_key=row_key,
                        column_key=column_key,
                        original_value=current.original_value,
                        selected_value=None,
                        timestamp=updated_at,
                    ))
                continue
            if current is not None and current == selected:
                continue
            new_events.append(ReviewOverrideEvent(
                kind=ReviewOverrideAction.SET,
                row_key=row_key,
                column_key=column_key,
                original_value=selected.original_value,
                selected_value=selected.human_value,
                timestamp=updated_at,
            ))
        events = (*self.events, *new_events)
        _validate_event_transitions(events)
        return ReviewOverrides(
            file_id=self.file_id,
            created_at=self.created_at,
            updated_at=updated_at,
            events=events,
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
            "events",
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
        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            raise InvalidReviewOverridesError("Stored review events must be a list.")
        events = tuple(ReviewOverrideEvent.from_payload(item) for item in raw_events)
        if any(event.timestamp < created_at or event.timestamp > updated_at for event in events):
            raise InvalidReviewOverridesError("Stored review event timestamps are out of order.")
        if any(
            later.timestamp < earlier.timestamp
            for earlier, later in zip(events, events[1:], strict=False)
        ):
            raise InvalidReviewOverridesError("Stored review events are not chronological.")
        _validate_event_transitions(events)
        return cls(
            file_id=expected_file_id,
            created_at=created_at,
            updated_at=updated_at,
            events=events,
            review_state=ReviewProgressState.from_payload(payload.get("review_state")),
        )

    def to_store(self) -> dict[str, object]:
        return {
            "schema_version": REVIEW_OVERRIDES_SCHEMA_VERSION,
            "file_id": self.file_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "events": [event.to_payload() for event in self.events],
            "review_state": self.review_state.to_payload(),
        }

    def to_snapshot_payload(self) -> dict[str, object]:
        """Return the browser contract without exposing the storage event log."""
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

    @cached_property
    def overrides(self) -> Mapping[str, Mapping[ColumnKey, CellOverride]]:
        active: dict[str, dict[ColumnKey, CellOverride]] = {}
        for event in self.events:
            columns = active.setdefault(event.row_key, {})
            if event.kind is ReviewOverrideAction.CLEAR:
                columns.pop(event.column_key, None)
            else:
                assert event.selected_value is not None
                columns[event.column_key] = CellOverride(
                    human_value=event.selected_value,
                    original_value=event.original_value,
                )
            if not columns:
                active.pop(event.row_key, None)
        return MappingProxyType({
            row_key: MappingProxyType(columns)
            for row_key, columns in active.items()
        })

    def human_values_by_row(self) -> dict[str, dict[ColumnKey, str]]:
        return {
            row_key: {
                column_key: override.human_value
                for column_key, override in columns.items()
            }
            for row_key, columns in self.overrides.items()
        }

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
        row_key = _row_key_from_payload(raw_row_key)
        parsed[row_key] = _parse_row_overrides(raw_columns)
    return parsed


def _validate_event_transitions(events: tuple[ReviewOverrideEvent, ...]) -> None:
    originals: dict[tuple[str, ColumnKey], str] = {}
    active: dict[tuple[str, ColumnKey], str] = {}
    for event in events:
        identity = (event.row_key, event.column_key)
        original = originals.setdefault(identity, event.original_value)
        if original != event.original_value:
            raise InvalidReviewOverridesError("Review event original values are inconsistent.")
        if event.kind is ReviewOverrideAction.CLEAR:
            if identity not in active:
                raise InvalidReviewOverridesError("A clear review event has no active choice.")
            active.pop(identity)
            continue
        assert event.selected_value is not None
        if active.get(identity) == event.selected_value:
            raise InvalidReviewOverridesError("A review event repeats the active choice.")
        active[identity] = event.selected_value


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


def _row_key_from_payload(value: object) -> str:
    if not isinstance(value, str) or not value.isdecimal() or int(value) < 1:
        raise InvalidReviewOverridesError("Review override row identity is invalid.")
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
    "ReviewOverrideAction",
    "ReviewOverrideEvent",
    "ReviewOverrides",
    "ReviewProgressState",
]
