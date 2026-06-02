"""Durable workflow facts that should survive browser or cache loss."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from src.domain.column_cde_map import ColumnCdeOverrides
from src.domain.column_renames import ColumnRenameSet
from src.domain.data_model_selection import DataModelSelection
from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_value

_FIELD_FILE_ID: Final = "file_id"
_FIELD_DATA_MODEL_KEY: Final = "data_model_key"
_FIELD_VERSION_NUMBER: Final = "version_number"
_FIELD_MANUAL_OVERRIDES: Final = "manual_overrides"
_FIELD_COLUMN_RENAMES: Final = "column_renames"


@dataclass(frozen=True)
class ConfirmedMappingChoices:
    """Stage 2 choices confirmed by the user before harmonization."""

    column_overrides: ColumnCdeOverrides
    column_renames: ColumnRenameSet

    @classmethod
    def from_raw(
        cls,
        manual_overrides: Mapping[str, str | None],
        column_renames: Mapping[str, str],
    ) -> ConfirmedMappingChoices:
        return cls(
            column_overrides=ColumnCdeOverrides.from_strings(manual_overrides),
            column_renames=ColumnRenameSet.from_dict(column_renames),
        )

    @classmethod
    def from_store(cls, payload: Mapping[str, object]) -> ConfirmedMappingChoices | None:
        raw_overrides = payload.get(_FIELD_MANUAL_OVERRIDES)
        raw_renames = payload.get(_FIELD_COLUMN_RENAMES)
        if raw_overrides is None and raw_renames is None:
            return None
        if not isinstance(raw_overrides, Mapping) or not isinstance(raw_renames, Mapping):
            return None

        manual_overrides: dict[str, str | None] = {}
        for column_key, cde_key in raw_overrides.items():
            if not isinstance(column_key, str):
                return None
            if cde_key is not None and not isinstance(cde_key, str):
                return None
            manual_overrides[column_key] = cde_key

        column_renames: dict[str, str] = {}
        for column_key, output_name in raw_renames.items():
            if not isinstance(column_key, str) or not isinstance(output_name, str):
                return None
            column_renames[column_key] = output_name

        return cls.from_raw(manual_overrides, column_renames)

    def to_store(self) -> dict[str, object]:
        return {
            _FIELD_MANUAL_OVERRIDES: self.column_overrides.to_strings(),
            _FIELD_COLUMN_RENAMES: self.column_renames.to_strings(),
        }


@dataclass(frozen=True)
class WorkflowState:
    """Small durable record for workflow choices keyed by uploaded file."""

    file_id: DatasetWorkflowId
    data_model_selection: DataModelSelection
    mapping_choices: ConfirmedMappingChoices | None = None

    @classmethod
    def from_selection(cls, file_id: DatasetWorkflowId | str, selection: DataModelSelection) -> WorkflowState:
        return cls(file_id=dataset_workflow_id_from_value(file_id), data_model_selection=selection)

    def with_mapping_choices(self, choices: ConfirmedMappingChoices) -> WorkflowState:
        return WorkflowState(
            file_id=self.file_id,
            data_model_selection=self.data_model_selection,
            mapping_choices=choices,
        )

    def to_store(self) -> dict[str, object]:
        payload: dict[str, object] = {
            _FIELD_FILE_ID: self.file_id,
            _FIELD_DATA_MODEL_KEY: self.data_model_selection.key,
            _FIELD_VERSION_NUMBER: self.data_model_selection.version_number,
        }
        if self.mapping_choices is not None:
            payload.update(self.mapping_choices.to_store())
        return payload

    @classmethod
    def from_store(cls, payload: object, file_id: DatasetWorkflowId | str) -> WorkflowState | None:
        if not isinstance(payload, Mapping):
            return None
        dataset_workflow_id = dataset_workflow_id_from_value(file_id)

        stored_file_id = payload.get(_FIELD_FILE_ID)
        data_model_key = payload.get(_FIELD_DATA_MODEL_KEY)
        version_number = payload.get(_FIELD_VERSION_NUMBER)
        if stored_file_id != dataset_workflow_id or not isinstance(data_model_key, str):
            return None
        if version_number is not None and not isinstance(version_number, int):
            return None

        return cls(
            file_id=dataset_workflow_id,
            data_model_selection=DataModelSelection.from_version_number(data_model_key, version_number),
            mapping_choices=ConfirmedMappingChoices.from_store(payload),
        )


__all__ = ["ConfirmedMappingChoices", "WorkflowState"]
