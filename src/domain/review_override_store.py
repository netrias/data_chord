"""Persist review override state through typed workflow storage.

Axis of change: how mutable Stage 4 review state is loaded, saved, and cleared.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from src.domain.review_overrides import ReviewOverrides
from src.domain.storage import UserContext, WorkflowFile, WorkflowNotFoundError, WorkflowStorage


class ReviewOverridesWorkflowNotFoundError(Exception):
    """Raised when review overrides are saved for an unknown workflow."""


def load_review_overrides(
    storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> ReviewOverrides | None:
    try:
        stored = storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    except WorkflowNotFoundError:
        return None
    if stored is None:
        return None
    return ReviewOverrides.from_store(stored.data, file_id)


def save_review_overrides_state(
    storage: WorkflowStorage,
    user: UserContext,
    *,
    file_id: str,
    overrides: object,
    review_state: object,
) -> ReviewOverrides:
    now = datetime.now(UTC)
    try:
        existing = storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    except WorkflowNotFoundError as exc:
        raise ReviewOverridesWorkflowNotFoundError(file_id) from exc

    current = ReviewOverrides.from_store(existing.data, file_id) if existing is not None else None
    saved = ReviewOverrides.create(
        file_id=file_id,
        created_at=current.created_at if current else now,
        updated_at=now,
        overrides=overrides,
        review_state=review_state if isinstance(review_state, Mapping) else {},
    )
    storage.write_json(
        user,
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        saved.to_store(),
        expected_version=existing.version if existing is not None else None,
    )
    return saved


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
    "ReviewOverridesWorkflowNotFoundError",
    "delete_review_overrides_state",
    "load_review_overrides",
    "save_review_overrides_state",
]
