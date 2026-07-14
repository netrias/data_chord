"""
Thin adapter converting netrias_client SDK types to kathmandu domain types.

Axis of change: SDK response shapes. Callers get stable domain types.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote

import httpx
from netrias_client import DataModelStoreError, NetriasAPIUnavailable, NetriasClient

from src.domain.cde import CDEInfo, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.cde_type_classification import classify_cde

# GC is the primary user right now — surface it first so the UI defaults to it
_PREFERRED_MODEL_KEY = "gc"


@dataclass(frozen=True)
class _DataModelStoreConfig:
    """HTTP settings needed for direct Data Model Store requests."""

    base_url: str
    api_key: str
    timeout: float


def list_data_model_summaries(client: NetriasClient | None) -> list[DataModelSummary]:
    """Why: decouples callers from SDK DataModel shape and versions tuple."""
    if client is None:
        return []
    models = client.list_data_models(include_versions=True)
    summaries = [
        DataModelSummary(
            data_model_key=m.key,
            label=m.name,
            versions=[
                DataModelVersionInfo(external_version_number=v.external_version_number)
                for v in m.versions or ()
            ],
        )
        for m in models
    ]
    summaries.sort(key=lambda s: (s.data_model_key != _PREFERRED_MODEL_KEY, s.data_model_key))
    return summaries


def fetch_cdes(
    client: NetriasClient | None,
    data_model_key: str,
    external_version_number: str,
) -> list[CDEInfo]:
    """Why: converts SDK CDE tuples to domain CDEInfo list.

    Initial cde_type is decided by classify_cde with has_pvs=None (PVs not
    fetched yet); PV / PASSTHROUGH refinement happens later via
    ``refine_cde_types_from_pvs``.
    """
    if client is None:
        return []
    sdk_cdes = client.list_cdes(
        model_key=data_model_key,
        version=external_version_number,
        include_description=True,
    )
    return [
        CDEInfo(
            cde_id=c.cde_id,
            cde_key=c.cde_key,
            description=c.description,
            cde_type=classify_cde(has_pvs=None),
        )
        for c in sdk_cdes
    ]


def refine_cde_types_from_pvs(
    catalog: CdeCatalog,
    pv_sets: CdePvCatalog,
) -> CdeCatalog:
    """Re-classify CDEs once PVs are known.

    For every CDE whose PV set has been fetched, the type is now decidable:
    non-empty PVs → ``PV``; empty → ``PASSTHROUGH``.
    Returns a new list — domain types are frozen.
    """
    refined: list[CDEInfo] = []
    for cde in catalog:
        if not pv_sets.has(cde.cde_key):
            refined.append(cde)
            continue
        has_pvs = bool(pv_sets.get(cde.cde_key))
        new_type = classify_cde(has_pvs=has_pvs)
        if new_type == cde.cde_type:
            refined.append(cde)
        else:
            refined.append(
                CDEInfo(
                    cde_id=cde.cde_id,
                    cde_key=cde.cde_key,
                    description=cde.description,
                    cde_type=new_type,
                )
            )
    return CdeCatalog.from_cdes(refined)


async def fetch_all_pvs_async(
    client: NetriasClient | None,
    data_model_key: str,
    external_version_number: str,
) -> CdePvCatalog:
    """Fetch all PVs for a model version in one request, grouped by CDE key."""
    if client is None:
        return CdePvCatalog.empty()
    config = _data_model_store_config(client)
    if config is None:
        return CdePvCatalog.empty()

    path = (
        f"/data-models/{quote(data_model_key, safe='')}"
        f"/versions/{quote(external_version_number, safe='')}/pvs"
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout)) as http_client:
            response = await http_client.get(
                f"{config.base_url.rstrip('/')}{path}",
                headers={"x-api-key": config.api_key},
            )
    except httpx.TimeoutException as exc:
        raise NetriasAPIUnavailable("data model store request timed out") from exc
    except httpx.HTTPError as exc:
        raise NetriasAPIUnavailable(f"data model store request failed: {exc}") from exc

    body = _response_json(response)
    return _pv_map_from_all_pvs_response(body)


def _pv_map_from_all_pvs_response(body: Mapping[str, object]) -> CdePvCatalog:
    grouped: dict[str, set[str]] = {}
    for item in _list_or_empty(body.get("items")):
        if not isinstance(item, Mapping):
            continue
        cde_key = item.get("cde_key")
        pv_value = item.get("pv_value")
        if isinstance(cde_key, str) and isinstance(pv_value, str):
            grouped.setdefault(cde_key, set()).add(pv_value)
    return CdePvCatalog({cde_key: frozenset(values) for cde_key, values in grouped.items()})


def _data_model_store_config(client: object) -> _DataModelStoreConfig | None:
    settings = getattr(client, "settings", None)
    endpoints = getattr(settings, "data_model_store_endpoints", None)
    base_url = getattr(endpoints, "base_url", None)
    api_key = getattr(settings, "api_key", None)
    timeout = getattr(settings, "timeout", None)
    if not isinstance(base_url, str) or not isinstance(api_key, str):
        return None
    return _DataModelStoreConfig(
        base_url=base_url,
        api_key=api_key,
        timeout=float(timeout) if isinstance(timeout, int | float) else 60.0,
    )


def _response_json(response: httpx.Response) -> Mapping[str, object]:
    if response.status_code >= 500:
        raise NetriasAPIUnavailable(f"data model store server error: {_error_message(response)}")
    if response.status_code >= 400:
        raise DataModelStoreError(f"data model store request failed: {_error_message(response)}")
    try:
        body = response.json()
    except ValueError as exc:
        raise DataModelStoreError(f"invalid JSON response: {exc}") from exc
    if not isinstance(body, Mapping):
        raise DataModelStoreError("unexpected response format: expected object")
    return cast(Mapping[str, object], body)


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    if isinstance(body, Mapping):
        for key in ("message", "detail", "error", "description"):
            value = body.get(key)
            if value:
                return str(value)
    return response.text[:200] or f"HTTP {response.status_code}"


def _list_or_empty(value: object) -> list[object]:
    return value if isinstance(value, list) else []
