"""Stage 2 orchestration: assemble column-detail payloads for the takeover.

Axis of change: how the backend bundles column profile + match counts +
selected PVs into one response. Pulls cached state (CDEs, profiles, PVs),
fetches missing PV sets, refines CDE types from the now-known PV presence, and
computes match counts. Domain primitives live elsewhere; this module is the
imperative shell.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool

from src.domain import dependencies
from src.domain.cde import CDEInfo, CdeType
from src.domain.column_profile import (
    ColumnProfile,
    build_column_profile_from_tabular,
    column_profile_to_payload,
)
from src.domain.data_model_adapter import (
    fetch_all_pvs_async,
    refine_cde_types_from_pvs,
)
from src.domain.data_model_cache import SessionCache, get_session_cache
from src.domain.match_counts import compute_column_overlap_by_cde, compute_match_counts

from .schemas import ColumnDetailResponse

_logger = logging.getLogger(__name__)


class ColumnDetailNotFound(Exception):
    """Raised when no profile exists for the requested file_id / column_key."""


@dataclass(frozen=True)
class CdeCatalogSnapshot:
    """CDE metadata plus PV sets as known at one point in a session."""

    cdes: list[CDEInfo]
    pv_sets: dict[str, frozenset[str]]

    @property
    def cde_types(self) -> dict[str, str]:
        return {cde.cde_key: cde.cde_type.value for cde in self.cdes}


async def compute_column_detail(
    file_id: str,
    column_key: str,
    selected_cde_key: str | None,
) -> ColumnDetailResponse:
    """Build the takeover's column-detail payload.

    ``selected_cde_key`` is the CDE the user currently has chosen for this
    column (AI rec by default, or their override). The response includes the
    PV list for that selection so the right pane can render immediately.

    Logically a query, but it memoizes two pieces of state on the cache:
    fetched PV sets and refined CDE types. Both are idempotent — repeated
    calls produce identical caches — so the CQS-violation cost is bounded.

    Example response::

        ColumnDetailResponse(
            column_key="diagnosis",
            profile=ColumnProfilePayload(...),
            match_counts={"tumor_diagnosis": 487, "icd10_diagnosis": 312},
            overlap_by_cde={"tumor_diagnosis": 0.82, "icd10_diagnosis": 0.53},
            cde_types={"tumor_diagnosis": "pv", "free_notes": "passthrough"},
            selected_pvs=["Adenocarcinoma", "Breast Cancer", ...],
        )
    """
    cache = get_session_cache(file_id)
    profile = await _get_or_build_column_profile(cache, file_id, column_key)
    catalog = await _get_cde_catalog_snapshot(cache)
    if not catalog.cdes:
        # CDEs not yet populated by the Stage 2 page. Return an empty match
        # map; the frontend can retry once the page-load completes.
        return ColumnDetailResponse(
            column_key=column_key,
            profile=column_profile_to_payload(profile),
        )

    distinct = frozenset(dv.value for dv in profile.distinct_values)
    return ColumnDetailResponse(
        column_key=column_key,
        profile=column_profile_to_payload(profile),
        match_counts=compute_match_counts(distinct, catalog.cdes, catalog.pv_sets),
        overlap_by_cde=compute_column_overlap_by_cde(distinct, catalog.cdes, catalog.pv_sets),
        cde_types=catalog.cde_types,
        selected_pvs=_selected_pvs(selected_cde_key, catalog.cdes, catalog.pv_sets),
    )


async def _get_or_build_column_profile(
    cache: SessionCache,
    file_id: str,
    column_key: str,
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
    cdes = cache.get_all_cdes()
    if not cdes:
        return CdeCatalogSnapshot(cdes=[], pv_sets={})

    await _ensure_pv_sets_fetched(cache)
    pv_sets = cache.get_all_pvs()
    refined = refine_cde_types_from_pvs(cdes, pv_sets)
    cache.replace_cdes(refined)
    return CdeCatalogSnapshot(cdes=refined, pv_sets=pv_sets)


async def _ensure_pv_sets_fetched(cache: SessionCache) -> None:
    """Side-effect: populate the cache with any PV sets not yet fetched."""
    missing_keys = cache.cde_keys_missing_pvs()
    if not missing_keys:
        return
    data_model_key, version_label = cache.get_model_info()
    if not data_model_key:
        return
    all_pvs = await fetch_all_pvs_async(data_model_key, version_label)
    cache.set_pvs_batch({key: all_pvs.get(key, frozenset()) for key in missing_keys})


def _selected_pvs(
    selected_cde_key: str | None,
    cdes: list[CDEInfo],
    pv_sets: Mapping[str, frozenset[str]],
) -> list[str] | None:
    """Return PVs sorted for display, or None for non-PV / unselected CDEs."""
    if not selected_cde_key:
        return None
    cde = next((c for c in cdes if c.cde_key == selected_cde_key), None)
    if cde is None or cde.cde_type != CdeType.PV:
        return None
    pvs = pv_sets.get(selected_cde_key)
    if not pvs:
        return None
    return sorted(pvs)
