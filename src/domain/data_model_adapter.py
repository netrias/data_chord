"""
Thin adapter converting netrias_client SDK types to kathmandu domain types.

Axis of change: SDK response shapes. Callers get stable domain types.
"""

from __future__ import annotations

import asyncio
import logging

from netrias_client import DataModelStoreError, NetriasAPIUnavailable

from src.domain.cde import CDEInfo, DataModelSummary
from src.domain.dependencies import get_netrias_client

_logger = logging.getLogger(__name__)


# GC is the primary user right now — surface it first so the UI defaults to it
_PREFERRED_MODEL_KEY = "gc"


def list_data_model_summaries() -> list[DataModelSummary]:
    """Why: decouples callers from SDK DataModel shape and versions tuple."""
    client = get_netrias_client()
    if client is None:
        return []
    models = client.list_data_models(include_versions=True)
    summaries = [
        DataModelSummary(
            key=m.key,
            label=m.name,
            versions=[v.version_label for v in (m.versions or ())],
        )
        for m in models
    ]
    summaries.sort(key=lambda s: (s.key != _PREFERRED_MODEL_KEY, s.key))
    return summaries


def get_latest_version(data_model_key: str) -> str:
    """Why: callers need a single version string, not SDK model traversal."""
    client = get_netrias_client()
    if client is None:
        _logger.warning("NetriasClient unavailable; defaulting to version 1")
        return "1"
    models = client.list_data_models(query=data_model_key, include_versions=True)
    for m in models:
        if m.key == data_model_key and m.versions:
            return m.versions[-1].version_label
    _logger.warning("No versions found for %s, defaulting to 1", data_model_key)
    return "1"


def fetch_cdes(data_model_key: str, version: str) -> list[CDEInfo]:
    """Why: converts SDK CDE tuples to domain CDEInfo list."""
    client = get_netrias_client()
    if client is None:
        return []
    sdk_cdes = client.list_cdes(data_model_key, version, include_description=True)
    return [
        CDEInfo(
            cde_id=c.cde_id,
            cde_key=c.cde_key,
            description=c.description,
            version_label=version,
        )
        for c in sdk_cdes
    ]


async def fetch_pvs_batch_async(
    data_model_key: str,
    version: str,
    cde_keys: list[str],
) -> dict[str, frozenset[str]]:
    """Why: natively async; individual failures degrade to empty frozenset."""
    client = get_netrias_client()
    if client is None or not cde_keys:
        return {}

    async def _fetch_one(key: str) -> tuple[str, frozenset[str]]:
        try:
            pv_set = await client.get_pv_set_async(data_model_key, version, key)
            return key, pv_set
        except (DataModelStoreError, NetriasAPIUnavailable):
            _logger.warning("PV fetch failed for %s; degrading to empty set", key)
            return key, frozenset()

    results = await asyncio.gather(*[_fetch_one(k) for k in cde_keys])
    return dict(results)
