"""
Read-only access to versioned data models, CDEs, and permissible values.

TODO: Migrate to netrias-client SDK when Data Model Store integration is added there.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from src.domain.cde import CDEInfo
from src.domain.config import get_data_model_store_api_key

logger = logging.getLogger(__name__)

BASE_URL = "https://85fnwlcuc2.execute-api.us-east-2.amazonaws.com/default"


@dataclass(frozen=True)
class DataModelSummary:
    key: str
    label: str
    versions: list[str]


@dataclass(frozen=True)
class DataModelVersion:
    version_label: str


class DataModelClientError(Exception):
    pass


class DataModelClient:
    """TODO: TEMPORARY - migrate to netrias-client SDK when ready."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or get_data_model_store_api_key()
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        """Lock protects lazy init from concurrent threads in fetch_pvs_batch."""
        with self._lock:
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
        client = self._get_client()
        try:
            resp = client.get(path, params=params or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.exception("Data Model Store API request failed: %s", path)
            raise DataModelClientError(f"API request failed: {path}") from e

    def list_data_models(self) -> list[DataModelSummary]:
        """API returns all data models with their versions in a single call."""
        data = self._get("/data-models", params={"include_versions": "true"})
        results: list[DataModelSummary] = []
        for item in data.get("items", []):
            key = item.get("key", "")
            label = item.get("label") or item.get("name") or key
            versions = [
                v.get("version_label", "v1") for v in item.get("versions", [])
            ]
            if key:
                results.append(DataModelSummary(key=key, label=label, versions=versions))
        return results

    def fetch_versions(self, data_model_key: str) -> list[DataModelVersion]:
        """API returns versions chronologically; caller uses [-1] to get latest."""
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
        data = self._get(
            _cde_path(data_model_key, version_label),
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
        """Returns frozenset for O(1) membership testing during validation."""
        try:
            data = self._get(f"{_cde_path(data_model_key, version_label, cde_key)}/pvs")
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
        """Parallel fetch reduces wall-clock time from O(n * latency) to O(latency)."""
        if not cde_keys:
            return {}
        result: dict[str, frozenset[str]] = {}
        with ThreadPoolExecutor(max_workers=min(len(cde_keys), 5)) as executor:
            futures = {
                executor.submit(self.fetch_pvs, data_model_key, version_label, key): key
                for key in cde_keys
            }
            for future in as_completed(futures):
                cde_key = futures[future]
                result[cde_key] = future.result()
        return result

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _cde_path(data_model_key: str, version_label: str, cde_key: str | None = None) -> str:
    """URL-encode path segments to prevent path traversal from external API values."""
    base = f"/data-models/{quote(data_model_key, safe='')}/versions/{quote(version_label, safe='')}/cdes"
    if cde_key is not None:
        return f"{base}/{quote(cde_key, safe='')}"
    return base
