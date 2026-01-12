"""
Temporary client for Data Model Store API.

TODO: This module is TEMPORARY. Fold into netrias-client SDK when ready.

Provides read-only access to versioned data models, CDEs, and permissible values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from src.domain.cde import CDEInfo
from src.domain.config import get_data_model_store_api_key

logger = logging.getLogger(__name__)

BASE_URL = "https://85fnwlcuc2.execute-api.us-east-2.amazonaws.com/default"


@dataclass(frozen=True)
class PermissibleValue:
    """why: Represent a single PV from API."""

    pv_id: int
    value: str
    description: str
    is_active: bool


@dataclass(frozen=True)
class DataModelVersion:
    """why: Represent an available version for a data model."""

    version_label: str


class DataModelClientError(Exception):
    """why: Distinguish Data Model Store API errors from other exceptions."""


class DataModelClient:
    """
    why: Fetch CDEs and PVs from Data Model Store API.

    TODO: TEMPORARY - migrate to netrias-client SDK when ready.
    NOTE: If removing 'route' breaks harmonization, re-add it here.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or get_data_model_store_api_key()
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        """why: Reuse client for connection pooling."""
        if self._client is None:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["x-api-key"] = self._api_key
            self._client = httpx.Client(
                base_url=BASE_URL,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """why: Centralize GET requests with error handling."""
        client = self._get_client()
        try:
            resp = client.get(path, params=params or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.exception("Data Model Store API request failed: %s", path)
            raise DataModelClientError(f"API request failed: {path}") from e

    def fetch_versions(self, data_model_key: str) -> list[DataModelVersion]:
        """
        Fetch available versions for a data model.

        Returns versions in API order (assume last is latest).
        """
        data = self._get("/data-models", params={
            "q": data_model_key,
            "include_versions": "true",
        })

        for model in data.get("items", []):
            if model.get("key") == data_model_key:
                versions = model.get("versions", [])
                return [
                    DataModelVersion(version_label=v.get("version_label", "v1"))
                    for v in versions
                ]
        return []

    def get_latest_version(self, data_model_key: str) -> str:
        """why: Determine latest version for a data model."""
        versions = self.fetch_versions(data_model_key)
        if not versions:
            logger.warning("No versions found for %s, defaulting to v1", data_model_key)
            return "v1"
        return versions[-1].version_label

    def fetch_cdes(
        self,
        data_model_key: str,
        version_label: str,
    ) -> list[CDEInfo]:
        """
        Fetch all CDEs for a data model version.

        Includes descriptions for UI tooltips.
        """
        data = self._get(
            f"/data-models/{data_model_key}/versions/{version_label}/cdes",
            params={"include_description": "true"},
        )

        return [
            CDEInfo(
                cde_id=item["cde_id"],
                cde_key=item["cde_key"],
                description=item.get("column_description"),
                version_label=version_label,
            )
            for item in data.get("items", [])
        ]

    def fetch_pvs(
        self,
        data_model_key: str,
        version_label: str,
        cde_key: str,
    ) -> frozenset[str]:
        """
        Fetch all PVs for a CDE.

        Returns frozenset for O(1) membership testing.
        Returns empty set if API unavailable (graceful degradation).
        """
        try:
            data = self._get(
                f"/data-models/{data_model_key}/versions/{version_label}/cdes/{cde_key}/pvs"
            )
            return frozenset(
                item["value"] for item in data.get("items", [])
            )
        except DataModelClientError:
            logger.warning("Failed to fetch PVs for %s, skipping validation", cde_key)
            return frozenset()

    def fetch_pvs_batch(
        self,
        data_model_key: str,
        version_label: str,
        cde_keys: list[str],
    ) -> dict[str, frozenset[str]]:
        """
        Fetch PVs for multiple CDEs.

        Returns dict mapping cde_key -> PV set.
        Continues on individual failures (graceful degradation).
        """
        result: dict[str, frozenset[str]] = {}
        for cde_key in cde_keys:
            result[cde_key] = self.fetch_pvs(data_model_key, version_label, cde_key)
        return result

    def fetch_pvs_with_metadata(
        self,
        data_model_key: str,
        version_label: str,
        cde_key: str,
    ) -> list[PermissibleValue]:
        """
        Fetch all PVs with full metadata for a CDE.

        Use when you need description/is_active info, not just value strings.
        """
        try:
            data = self._get(
                f"/data-models/{data_model_key}/versions/{version_label}/cdes/{cde_key}/pvs"
            )
            return [
                PermissibleValue(
                    pv_id=item.get("pv_id", 0),
                    value=item.get("value", ""),
                    description=item.get("description", ""),
                    is_active=item.get("is_active", True),
                )
                for item in data.get("items", [])
            ]
        except DataModelClientError:
            logger.warning("Failed to fetch PV metadata for %s", cde_key)
            return []

    def close(self) -> None:
        """why: Clean up HTTP client resources."""
        if self._client is not None:
            self._client.close()
            self._client = None
