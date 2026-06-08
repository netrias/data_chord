"""Stage 2 use cases for mapping review and confirmed workflow state."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool

from src.domain import dependencies
from src.domain.cde import CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_profile import (
    ColumnProfile,
    build_column_profile_from_tabular,
    column_profile_to_payload,
)
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.data_model_adapter import (
    fetch_all_pvs_async,
    refine_cde_types_from_pvs,
)
from src.domain.data_model_cache import SessionCache, get_session_cache
from src.domain.match_counts import compute_column_overlap_by_cde, compute_match_counts
from src.domain.storage import UserContext, WorkflowStorage
from src.domain.workflow_state import ConfirmedMappingChoices
from src.domain.workflow_state_store import (
    WorkflowStateConflictError,
    WorkflowStateNotFoundError,
    save_confirmed_mapping_choices_to_state,
)

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
    file_id: str,
    column_key: str,
    selected_cde_key: str | None,
) -> ColumnDetailResponse:
    """Build the takeover's column-detail payload."""
    source_column_key = column_key_from_string(column_key)
    cache = get_session_cache(file_id)
    profile = await _get_or_build_column_profile(cache, file_id, source_column_key)
    catalog = await _get_cde_catalog_snapshot(cache)
    if catalog.catalog.is_empty():
        # CDEs not yet populated by the Stage 2 page. Return an empty match
        # map; the frontend can retry once the page-load completes.
        return ColumnDetailResponse(
            column_key=str(source_column_key),
            profile=column_profile_to_payload(profile),
        )

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
    except WorkflowStateConflictError as exc:
        raise MappingWorkflowStateConflictError() from exc
    return SaveMappingChoicesResponse(file_id=payload.file_id)


async def _get_or_build_column_profile(
    cache: SessionCache,
    file_id: str,
    column_key: ColumnKey,
) -> ColumnProfile:
    profile = cache.get_column_profile(column_key)
    if profile is not None:
        return profile

    storage = dependencies.get_upload_storage()
    meta = storage.load(file_id)
    if meta is None:
        raise ColumnDetailNotFound(f"No upload found for {file_id}")

    profile = await run_in_threadpool(
        build_column_profile_from_tabular,
        meta.saved_path,
        column_key,
        meta.selected_sheet,
    )
    if profile is None:
        raise ColumnDetailNotFound(f"No profile available for {file_id}/{column_key}")
    cache.set_column_profile(profile)
    return profile


async def _get_cde_catalog_snapshot(cache: SessionCache) -> CdeCatalogSnapshot:
    catalog = cache.get_cde_catalog()
    if catalog.is_empty():
        return CdeCatalogSnapshot(catalog=CdeCatalog.empty(), pv_sets=CdePvCatalog.empty())

    await _ensure_pv_sets_fetched(cache)
    pv_sets = cache.get_all_pvs()
    refined = refine_cde_types_from_pvs(catalog, pv_sets)
    cache.replace_cde_catalog(refined)
    return CdeCatalogSnapshot(catalog=refined, pv_sets=pv_sets)


async def _ensure_pv_sets_fetched(cache: SessionCache) -> None:
    """Side-effect: populate the cache with any PV sets not yet fetched."""
    missing_keys = cache.cde_keys_missing_pvs()
    if not missing_keys:
        return
    data_model_version = cache.get_data_model_version()
    if data_model_version is None:
        return
    all_pvs = await fetch_all_pvs_async(data_model_version.data_model_key, data_model_version.external_version_number)
    cache.set_pvs_batch(all_pvs.with_defaults(missing_keys))


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
