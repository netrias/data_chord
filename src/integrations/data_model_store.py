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
    config = _data_model_store_config(client)
    summaries = _list_data_model_summaries_direct(config) if config else _list_data_model_summaries_from_sdk(client)
    summaries.sort(key=lambda s: (s.data_model_key != _PREFERRED_MODEL_KEY, s.data_model_key))
    return summaries


def _list_data_model_summaries_from_sdk(client: NetriasClient) -> list[DataModelSummary]:
    models = client.list_data_models(include_versions=True)
    return [
        DataModelSummary(
            data_model_key=m.key,
            label=m.name,
            versions=[
                _version_info_from_label(
                    getattr(v, "external_version_number", getattr(v, "version_label", "")),
                    is_default=index == len(m.versions or ()) - 1,
                )
                for index, v in enumerate(m.versions or ())
            ],
        )
        for m in models
    ]


def _list_data_model_summaries_direct(config: _DataModelStoreConfig) -> list[DataModelSummary]:
    """Fetch directly so the app can keep version_number and external labels."""
    try:
        response = httpx.get(
            f"{config.base_url.rstrip('/')}/data-models",
            headers={"x-api-key": config.api_key},
            params={"include_versions": "true"},
            timeout=config.timeout,
        )
    except httpx.TimeoutException as exc:
        raise NetriasAPIUnavailable("data model store request timed out") from exc
    except httpx.HTTPError as exc:
        raise NetriasAPIUnavailable(f"data model store request failed: {exc}") from exc

    body = _response_json(response)
    items = body.get("items")
    if not isinstance(items, list):
        return []
    return [_summary_from_item(item) for item in items if isinstance(item, Mapping)]


def _summary_from_item(item: Mapping[str, object]) -> DataModelSummary:
    versions = [
        version
        for raw in _list_or_empty(item.get("versions"))
        if isinstance(raw, Mapping) and (version := _version_info_from_item(raw)) is not None
    ]
    return DataModelSummary(
        data_model_key=str(item.get("key", "")),
        label=str(item.get("name", "")),
        versions=versions,
    )


def _version_info_from_item(item: Mapping[str, object]) -> DataModelVersionInfo | None:
    raw_number = item.get("version_number")
    version_number = _int_or_none(raw_number)
    if version_number is None:
        return None
    raw_label = item.get("version_label")
    external = item.get("external_version_number")
    if not isinstance(external, str) or not external.strip():
        return None
    return DataModelVersionInfo(
        version_label=str(raw_label) if raw_label else str(version_number),
        version_number=version_number,
        external_version_number=external.strip(),
        is_default=bool(item.get("is_default", False)),
    )


def _version_info_from_label(version_label: str, is_default: bool = False) -> DataModelVersionInfo:
    """Compatibility adapter for older SDK objects that only expose version_label."""
    return DataModelVersionInfo(
        version_label=version_label,
        version_number=_int_or_none(version_label) or 1,
        external_version_number=version_label,
        is_default=is_default,
    )


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
    dms_version = _dms_version_for_external(client, data_model_key, external_version_number)
    sdk_cdes = client.list_cdes(
        model_key=data_model_key,
        version=dms_version,
        include_description=True,
    )
    return [
        CDEInfo(
            cde_id=c.cde_id,
            cde_key=c.cde_key,
            description=c.description,
            version_label=external_version_number,
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
                    version_label=cde.version_label,
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
    dms_version = await _dms_version_for_external_async(config, data_model_key, external_version_number)

    path = (
        f"/data-models/{quote(data_model_key, safe='')}"
        f"/versions/{quote(dms_version, safe='')}/pvs"
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


def _dms_version_for_external(client: NetriasClient, data_model_key: str, external_version_number: str) -> str:
    config = _data_model_store_config(client)
    if config is None:
        return external_version_number
    summaries = _list_data_model_summaries_direct(config)
    return _find_dms_version_number(
        summaries=summaries,
        data_model_key=data_model_key,
        external_version_number=external_version_number,
    )


async def _dms_version_for_external_async(
    config: _DataModelStoreConfig,
    data_model_key: str,
    external_version_number: str,
) -> str:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout)) as http_client:
            response = await http_client.get(
                f"{config.base_url.rstrip('/')}/data-models",
                headers={"x-api-key": config.api_key},
                params={"include_versions": "true"},
            )
    except httpx.TimeoutException as exc:
        raise NetriasAPIUnavailable("data model store request timed out") from exc
    except httpx.HTTPError as exc:
        raise NetriasAPIUnavailable(f"data model store request failed: {exc}") from exc

    body = _response_json(response)
    items = body.get("items")
    summaries = (
        [_summary_from_item(item) for item in items if isinstance(item, Mapping)]
        if isinstance(items, list)
        else []
    )
    return _find_dms_version_number(
        summaries=summaries,
        data_model_key=data_model_key,
        external_version_number=external_version_number,
    )


def _find_dms_version_number(
    *,
    summaries: list[DataModelSummary],
    data_model_key: str,
    external_version_number: str,
) -> str:
    for summary in summaries:
        if summary.data_model_key != data_model_key:
            continue
        for version in summary.versions:
            if version.external_version_number == external_version_number:
                return str(version.version_number)
    raise DataModelStoreError(
        "data model store version lookup failed: "
        f"no version_number found for {data_model_key} external version {external_version_number}"
    )


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


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.removeprefix("v")
        return int(cleaned) if cleaned.isdigit() else None
    return None
