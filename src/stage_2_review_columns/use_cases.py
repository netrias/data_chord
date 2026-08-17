"""Stage 2 use cases for mapping review and confirmed workflow state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from netrias_client import read_tabular

from src.app.session_cache import SessionCache, get_session_cache
from src.domain.cde import CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_profile import (
    ColumnProfile,
    build_column_profile,
    column_profile_to_payload,
)
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.match_counts import compute_column_overlap_by_cde, compute_match_counts
from src.domain.reference_data import ReferenceDataRepository
from src.domain.workflow_state import ConfirmedMappingChoices
from src.persistence.workflow_artifacts import load_upload_artifact
from src.persistence.workflow_state_store import (
    WorkflowStateConflictError,
    WorkflowStateNotFoundError,
    WorkflowStateUnreadableError,
    load_workflow_state,
    save_confirmed_mapping_choices_to_state,
)
from src.storage import UploadStorage, UserContext, WorkflowStorage

from .schemas import ColumnDetailResponse, SaveMappingChoicesRequest, SaveMappingChoicesResponse


class ColumnDetailNotFound(Exception):
    """Raised when no profile exists for the requested file_id / column_key."""


class MappingWorkflowStateNotFoundError(Exception):
    """Raised when Stage 2 choices are saved before Stage 1 creates workflow state."""


class MappingWorkflowStateConflictError(Exception):
    """Raised when Stage 2 choices race with another workflow state update."""


@dataclass(frozen=True)
class CdeCatalogSnapshot:
    """CDE metadata plus PV sets as known at one point in a session."""

    catalog: CdeCatalog
    pv_sets: CdePvCatalog

    @property
    def cde_types(self) -> dict[str, str]:
        return self.catalog.cde_types_payload()


async def compute_column_detail(
    *,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    column_key: str,
    selected_cde_key: str | None,
    reference_repository: ReferenceDataRepository,
) -> ColumnDetailResponse:
    """Build the takeover's column-detail payload."""
    # Durable state establishes both workflow existence and ownership before a
    # process-local cache can reveal anything about the workflow.
    loaded_state = load_workflow_state(
        workflow_storage,
        user,
        file_id,
    )
    if loaded_state is None:
        raise ColumnDetailNotFound(f"No workflow found for {file_id}")
    source_column_key = column_key_from_string(column_key)
    cache = get_session_cache(file_id, owner_user_id=user.user_id)
    profile = await _get_or_build_column_profile(
        cache,
        upload_storage,
        workflow_storage,
        user,
        file_id,
        source_column_key,
    )
    model = await run_in_threadpool(reference_repository.load_model, loaded_state.state.data_model_version)
    catalog = CdeCatalogSnapshot(model.catalog, model.pvs)

    distinct = frozenset(dv.value for dv in profile.distinct_values)
    return ColumnDetailResponse(
        column_key=str(source_column_key),
        profile=column_profile_to_payload(profile),
        match_counts=compute_match_counts(distinct, catalog.catalog, catalog.pv_sets),
        overlap_by_cde=compute_column_overlap_by_cde(distinct, catalog.catalog, catalog.pv_sets),
        cde_types=catalog.cde_types,
        selected_pvs=_selected_pvs(selected_cde_key, catalog.catalog, catalog.pv_sets),
    )


def save_confirmed_mapping_choices(
    *,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    payload: SaveMappingChoicesRequest,
) -> SaveMappingChoicesResponse:
    """Persist confirmed Stage 2 choices as durable workflow state."""
    choices = ConfirmedMappingChoices.from_raw(payload.manual_overrides, payload.column_renames)
    try:
        save_confirmed_mapping_choices_to_state(
            workflow_storage,
            user,
            payload.file_id,
            choices,
        )
    except WorkflowStateNotFoundError as exc:
        raise MappingWorkflowStateNotFoundError() from exc
    except (WorkflowStateConflictError, WorkflowStateUnreadableError) as exc:
        raise MappingWorkflowStateConflictError() from exc
    return SaveMappingChoicesResponse(file_id=payload.file_id)


async def _get_or_build_column_profile(
    cache: SessionCache,
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    column_key: ColumnKey,
) -> ColumnProfile:
    profile = cache.get_column_profile(column_key)
    if profile is not None:
        return profile

    meta = load_upload_artifact(upload_storage, workflow_storage, user, file_id)
    if meta is None:
        raise ColumnDetailNotFound(f"No upload found for {file_id}")

    profile = await run_in_threadpool(
        _build_column_profile_from_tabular,
        meta.saved_path,
        column_key,
        meta.selected_sheet,
    )
    if profile is None:
        raise ColumnDetailNotFound(f"No profile available for {file_id}/{column_key}")
    cache.set_column_profile(profile)
    return profile


def _build_column_profile_from_tabular(
    tabular_path: Path,
    column_key: ColumnKey,
    sheet_name: str | None,
) -> ColumnProfile | None:
    """Read the Stage 1 artifact and convert one source column into a profile."""
    dataset = read_tabular(tabular_path, sheet_name=sheet_name)
    column = next(
        (candidate for candidate in dataset.columns if candidate.key == str(column_key)),
        None,
    )
    if column is None:
        return None
    return build_column_profile(
        column.key,
        (row[column.index] if column.index < len(row) else "" for row in dataset.rows),
    )


def _selected_pvs(
    selected_cde_key: str | None,
    catalog: CdeCatalog,
    pv_sets: CdePvCatalog,
) -> list[str] | None:
    """Return PVs sorted for display, or None for non-PV / unselected CDEs."""
    if not selected_cde_key:
        return None
    cde = catalog.get(selected_cde_key)
    if cde is None or cde.cde_type != CdeType.PV:
        return None
    pvs = pv_sets.get(selected_cde_key)
    if not pvs:
        return None
    return sorted(pvs)


__all__ = [
    "ColumnDetailNotFound",
    "MappingWorkflowStateConflictError",
    "MappingWorkflowStateNotFoundError",
    "compute_column_detail",
    "save_confirmed_mapping_choices",
]
