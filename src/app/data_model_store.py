"""App-facing Data Model Store operations with configured client wiring."""

from __future__ import annotations

import logging

from src.app.dependencies import get_netrias_client
from src.app.session_cache import get_session_cache
from src.domain.cde import CDEInfo, DataModelSummary
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.integrations import data_model_store as adapter

_logger = logging.getLogger(__name__)


def list_data_model_summaries() -> list[DataModelSummary]:
    return adapter.list_data_model_summaries(get_netrias_client())


def fetch_cdes(data_model_key: str, external_version_number: str) -> list[CDEInfo]:
    return adapter.fetch_cdes(get_netrias_client(), data_model_key, external_version_number)


def refine_cde_types_from_pvs(
    catalog: CdeCatalog,
    pv_sets: CdePvCatalog,
) -> CdeCatalog:
    return adapter.refine_cde_types_from_pvs(catalog, pv_sets)


async def fetch_all_pvs_async(
    data_model_key: str,
    external_version_number: str,
) -> CdePvCatalog:
    return await adapter.fetch_all_pvs_async(
        get_netrias_client(),
        data_model_key,
        external_version_number,
    )


def populate_cde_cache(file_id: str, data_model_version: DataModelVersionReference) -> None:
    """PV validation in Stage 3+ requires data model identity and version before PV fetch."""
    cdes = fetch_cdes(data_model_version.data_model_key, data_model_version.external_version_number)
    cache = get_session_cache(file_id)
    cache.set_cdes(
        cdes,
        data_model_key=data_model_version.data_model_key,
        external_version_number=data_model_version.external_version_number,
    )

    _logger.info(
        "Populated CDE cache from Data Model Store API",
        extra={
            "file_id": file_id,
            "cde_count": len(cdes),
            "data_model": data_model_version.data_model_key,
            "external_version_number": data_model_version.external_version_number,
        },
    )


__all__ = [
    "fetch_all_pvs_async",
    "fetch_cdes",
    "list_data_model_summaries",
    "populate_cde_cache",
    "refine_cde_types_from_pvs",
]
