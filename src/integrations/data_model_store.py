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
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.cde_type_classification import classify_cde


@dataclass(frozen=True)
class _DataModelStoreConfig:
    """HTTP settings needed for direct Data Model Store requests."""

    base_url: str
    api_key: str
    timeout: float


def list_data_model_summaries(client: NetriasClient | None) -> list[DataModelSummary]:
    """Why: decouples callers from SDK DataModel shape and versions tuple."""
    if client is None:
        raise NetriasAPIUnavailable("data model store client is unavailable")
    models = client.list_data_models(include_versions=True)
    return [
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


def fetch_cdes(
    client: NetriasClient | None,
    data_model_key: str,
    external_version_number: str,
) -> list[CDEInfo]:
    """Why: converts SDK CDE tuples to domain CDEInfo list.

    Initial cde_type is decided by classify_cde with has_pvs=None. The domain
    refines it after PV lookup.
    """
    if client is None:
        raise NetriasAPIUnavailable("data model store client is unavailable")
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


async def fetch_all_pvs_async(
    client: NetriasClient | None,
    data_model_key: str,
    external_version_number: str,
) -> CdePvCatalog:
    """Fetch all PVs for a model version in one request, grouped by CDE key."""
    if client is None:
        raise NetriasAPIUnavailable("data model store client is unavailable")
    config = _data_model_store_config(client)
    if config is None:
        raise DataModelStoreError("data model store client configuration is incomplete")

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
    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        raise DataModelStoreError("unexpected PV response format: items must be a list")
    grouped: dict[str, set[str]] = {}
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            raise DataModelStoreError(f"unexpected PV response item at index {index}: expected object")
        cde_key = item.get("cde_key")
        pv_value = item.get("pv_value")
        if not isinstance(cde_key, str) or not cde_key or not isinstance(pv_value, str):
            raise DataModelStoreError(f"unexpected PV response item at index {index}: invalid cde_key or pv_value")
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
