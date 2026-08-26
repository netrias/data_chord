"""Persist review override state through typed workflow storage.

Axis of change: how mutable Stage 4 review state is loaded, saved, and cleared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.domain.review_overrides import (
    InvalidReviewOverridesError,
    ReviewOverrides,
    ReviewProgressState,
)
from src.storage import (
    UserContext,
    VersionToken,
    WorkflowConflictError,
    WorkflowFile,
    WorkflowJsonUnreadableError,
    WorkflowNotFoundError,
    WorkflowStorage,
)


class ReviewOverridesWorkflowNotFoundError(Exception):
    """Raised when review overrides are saved for an unknown workflow."""


class ReviewOverridesStoreConflictError(Exception):
    """Raised when review override state changed after the caller read it."""


class ReviewOverridesUnreadableError(Exception):
    """Raised when stored review override state violates the current schema."""


@dataclass(frozen=True)
class ReviewOverridesRecord:
    """Decoded active review overrides and their opaque storage version."""

    value: ReviewOverrides
    version: VersionToken


@dataclass(frozen=True)
class SavedReviewOverrides:
    """Successful active-state write."""

    value: ReviewOverrides
    version: VersionToken


def load_review_overrides(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> ReviewOverrides | None:
    record = load_review_overrides_record(storage, user, file_id)
    return record.value if record is not None else None


def load_review_overrides_record(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> ReviewOverridesRecord | None:
    try:
        stored = storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    except WorkflowNotFoundError:
        return None
    except WorkflowJsonUnreadableError as exc:
        raise ReviewOverridesUnreadableError(f"Unreadable review overrides for {file_id}.") from exc
    if stored is None:
        return None
    try:
        value = ReviewOverrides.from_store(stored.data, file_id)
    except InvalidReviewOverridesError as exc:
        raise ReviewOverridesUnreadableError(f"Unreadable review overrides for {file_id}.") from exc
    return ReviewOverridesRecord(value=value, version=stored.version)


def save_review_overrides_state(
    storage: WorkflowStorage,
    user: UserContext,
    *,
    file_id: str,
    overrides: object,
    review_state: ReviewProgressState,
    expected_version: VersionToken | None = None,
) -> SavedReviewOverrides:
    now = datetime.now(UTC)
    try:
        existing = storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    except WorkflowNotFoundError as exc:
        raise ReviewOverridesWorkflowNotFoundError(file_id) from exc
    except WorkflowJsonUnreadableError as exc:
        raise ReviewOverridesUnreadableError(f"Unreadable review overrides for {file_id}.") from exc

    try:
        current = ReviewOverrides.from_store(existing.data, file_id) if existing is not None else None
    except InvalidReviewOverridesError as exc:
        raise ReviewOverridesUnreadableError(f"Unreadable review overrides for {file_id}.") from exc
    # Preserve created_at across saves so Stage 5 can distinguish the first
    # review session from later edits.
    saved = ReviewOverrides.create(
        file_id=file_id,
        created_at=current.created_at if current else now,
        updated_at=now,
        overrides=overrides,
        review_state=review_state,
    )
    try:
        stored = storage.write_json(
            user,
            file_id,
            WorkflowFile.REVIEW_OVERRIDES,
            saved.to_store(),
            expected_version=expected_version,
        )
    except WorkflowConflictError as exc:
        raise ReviewOverridesStoreConflictError(file_id) from exc
    return SavedReviewOverrides(value=saved, version=stored.version)


def delete_review_overrides_state(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> bool:
    try:
        return storage.delete_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    except WorkflowNotFoundError:
        return False


__all__ = [
    "ReviewOverridesRecord",
    "ReviewOverridesStoreConflictError",
    "ReviewOverridesUnreadableError",
    "ReviewOverridesWorkflowNotFoundError",
    "SavedReviewOverrides",
    "delete_review_overrides_state",
    "load_review_overrides",
    "load_review_overrides_record",
    "save_review_overrides_state",
]
