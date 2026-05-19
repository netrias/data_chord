"""Stage 2 use cases for confirmed mapping workflow state."""

from __future__ import annotations

from src.domain.storage import FileStore
from src.domain.workflow_state import ConfirmedMappingChoices

from .schemas import SaveMappingChoicesRequest, SaveMappingChoicesResponse


class MappingWorkflowStateNotFoundError(Exception):
    """Raised when Stage 2 choices are saved before Stage 1 creates workflow state."""


def save_confirmed_mapping_choices(
    *,
    file_store: FileStore,
    payload: SaveMappingChoicesRequest,
) -> SaveMappingChoicesResponse:
    """Persist confirmed Stage 2 choices as durable workflow state."""
    state = file_store.load_workflow_state(payload.file_id)
    if state is None:
        raise MappingWorkflowStateNotFoundError()

    choices = ConfirmedMappingChoices.from_raw(payload.manual_overrides, payload.column_renames)
    file_store.save_workflow_state(state.with_mapping_choices(choices))
    return SaveMappingChoicesResponse(file_id=payload.file_id)


__all__ = [
    "MappingWorkflowStateNotFoundError",
    "save_confirmed_mapping_choices",
]
