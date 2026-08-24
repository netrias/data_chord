"""Focused proof for the Stage 5 ReviewOverrides v3 projection."""

from __future__ import annotations

from datetime import UTC, datetime

from src.domain.columns import column_key_from_string
from src.domain.harmonization import MatchFidelity
from src.domain.manifest import ManifestRow
from src.domain.review_overrides import (
    ReviewOverrideAction,
    ReviewOverrideEvent,
    ReviewOverrides,
    ReviewProgressState,
)
from src.stage_5_review_summary.use_cases import _build_history, _manifest_row_projection


def _manifest_row() -> ManifestRow:
    return ManifestRow(
        job_id="job-1",
        column_id=0,
        column_name="diagnosis",
        to_harmonize="source",
        top_harmonization="AI value",
        ontology_id=None,
        top_harmonizations=["AI value"],
        match_fidelity=MatchFidelity.STRONG,
        error=None,
        row_indices=[0, 1],
    )


def _review_overrides(*events: ReviewOverrideEvent) -> ReviewOverrides:
    now = datetime(2026, 8, 24, 12, 2, tzinfo=UTC)
    return ReviewOverrides(
        file_id="a" * 32,
        created_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        updated_at=now,
        events=events,
        review_state=ReviewProgressState(),
    )


def _set_event() -> ReviewOverrideEvent:
    return ReviewOverrideEvent(
        kind=ReviewOverrideAction.SET,
        row_key="2",
        column_key=column_key_from_string("col_0000"),
        original_value="source",
        selected_value="Human value",
        timestamp=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
    )


def _clear_event() -> ReviewOverrideEvent:
    return ReviewOverrideEvent(
        kind=ReviewOverrideAction.CLEAR,
        row_key="2",
        column_key=column_key_from_string("col_0000"),
        original_value="source",
        selected_value=None,
        timestamp=datetime(2026, 8, 24, 12, 1, tzinfo=UTC),
    )


def test_history_is_limited_to_the_selected_one_based_row() -> None:
    """Given a row-specific event, its history does not leak to another row."""
    row = _manifest_row()
    overrides = _review_overrides(_set_event())

    # Given: one event targets only source row 2
    first_row_history = _build_history(row, None, None, overrides, 0)
    second_row_history = _build_history(row, None, None, overrides, 1)

    # When: histories are built for each manifest row occurrence
    first_sources = [step.source for step in first_row_history]
    second_sources = [step.source for step in second_row_history]

    # Then: only row 2 contains the reviewer decision
    assert first_sources == ["original", "ai"]
    assert second_sources == ["original", "ai", "user"]
    assert second_row_history[-1].value == "Human value"


def test_manifest_projection_keeps_stage3_fields_and_adds_review_state() -> None:
    """Given v3 events, the ZIP projection is immutable and row-aware."""
    row = _manifest_row()
    event = _set_event()

    # Given: a Stage 3 row and one active reviewer event
    projection = _manifest_row_projection(row, _review_overrides(event))

    # When: the manifest projection is serialized for the ZIP
    # Then: Stage 3 facts stay intact and review state is explicit
    assert projection["to_harmonize"] == "source"
    assert projection["top_harmonization"] == "AI value"
    assert projection["row_indices"] == [0, 1]
    assert projection["active_values"] == {"1": "AI value", "2": "Human value"}
    assert projection["review_events"] == [{
        "kind": "set",
        "row_key": "2",
        "column_key": "col_0000",
        "original_value": "source",
        "selected_value": "Human value",
        "timestamp": "2026-08-24T12:00:00+00:00",
    }]
    assert "manual_overrides" not in projection


def test_history_exposes_clear_as_a_review_action() -> None:
    """Given a clear event, history records the action and restored baseline."""
    row = _manifest_row()

    # Given: the reviewer selects another value, then clears that choice.
    history = _build_history(
        row,
        None,
        None,
        _review_overrides(_set_event(), _clear_event()),
        1,
    )

    # When: the row history is read
    review_steps = [step for step in history if step.source == "user"]

    # Then: both valid decisions remain visible, including the clear action.
    assert [step.action for step in review_steps] == ["set", "clear"]
    clear_step = review_steps[-1]
    assert clear_step.action == "clear"
    assert clear_step.source == "user"
    assert clear_step.value == "AI value"
