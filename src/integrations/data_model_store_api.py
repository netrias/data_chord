"""Read-only reference data from the Data Model Store HTTP API."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceDataUnavailableError,
    ReferenceModel,
    ReferenceModelNotFoundError,
)

# The service refuses a response over 5.5 MB. The largest model is about 2 MB,
# so this page size leaves room for one several times larger.
_PAGE_SIZE = 5000
_TIMEOUT_SECONDS = 60


class HttpTransport(Protocol):
    """Injected so tests substitute a fake, as the DynamoDB repository takes a table."""

    def get(self, url: str, headers: Mapping[str, str], timeout: int) -> bytes: ...


class UrllibTransport:
    """Raises urllib errors unchanged; the repository translates them."""

    def get(self, url: str, headers: Mapping[str, str], timeout: int) -> bytes:
        request = urllib.request.Request(url, headers=dict(headers))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return bytes(response.read())


@dataclass(frozen=True)
class CollapsedPlacement:
    """Reported rather than raised; the domain has no node to attribute a difference to."""

    cde_key: str
    node_count: int
    descriptions: tuple[str | None, ...]


class DataModelStoreApiRepository:
    """The store keys CDEs by (node, cde_key); this domain has no node, so they collapse."""

    def __init__(
        self, base_url: str, api_key: str, transport: HttpTransport | None = None
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required")
        if not api_key.strip():
            raise ValueError("api_key is required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport: HttpTransport = transport or UrllibTransport()
        # Populated by the most recent load_model call.
        self.last_collapsed: tuple[CollapsedPlacement, ...] = ()

    def list_models(self) -> tuple[DataModelSummary, ...]:
        payload = self._get("/data-models", {"include_versions": "true"})
        summaries: list[DataModelSummary] = []
        for item in payload.get("items", []):
            key = str(item.get("key", "")).strip()
            if not key:
                raise ReferenceDataCorruptError("Data model is missing its key")
            versions = [
                DataModelVersionInfo(external_version_number=str(v["external_version_number"]))
                for v in item.get("versions", [])
            ]
            summaries.append(
                DataModelSummary(
                    data_model_key=key,
                    label=str(item.get("name") or key),
                    versions=versions,
                )
            )
        return tuple(summaries)

    def load_model(self, version: DataModelVersionReference) -> ReferenceModel:
        model_key = version.data_model_key
        external = version.external_version_number
        prefix = (
            f"/data-models/{urllib.parse.quote(model_key)}"
            f"/versions/{urllib.parse.quote(external)}"
        )

        cde_payload = self._get(f"{prefix}/cdes", {"include_description": "true"})
        catalog, collapsed = self._collapse(cde_payload.get("items", []))
        self.last_collapsed = collapsed

        values = self._load_values(f"{prefix}/pvs")
        # Every CDE needs an entry, including one with no values, because the
        # domain requires the two key sets to match exactly.
        complete = {cde.cde_key: values.get(cde.cde_key, frozenset()) for cde in catalog}

        return ReferenceModel(
            version=version,
            label=self._label_for(model_key),
            catalog=CdeCatalog.from_cdes(
                # cde_type is decided here rather than from the service's
                # data_type, because the domain only distinguishes "has a value
                # set" from "does not", and checks that against the values.
                CDEInfo(
                    cde_id=cde.cde_id,
                    cde_key=cde.cde_key,
                    description=cde.description,
                    cde_type=CdeType.PV if complete[cde.cde_key] else CdeType.PASSTHROUGH,
                )
                for cde in catalog
            ),
            pvs=CdePvCatalog.from_mapping(complete),
        )

    def _collapse(
        self, items: list[dict[str, Any]]
    ) -> tuple[tuple[CDEInfo, ...], tuple[CollapsedPlacement, ...]]:
        """Keeps the first placement of each key, because ReferenceModel forbids duplicates."""
        by_key: dict[str, CDEInfo] = {}
        seen: defaultdict[str, list[str | None]] = defaultdict(list)
        for item in items:
            cde_key = str(item.get("cde_key", "")).strip()
            if not cde_key:
                raise ReferenceDataCorruptError("CDE is missing its key")
            description = item.get("cde_description")
            seen[cde_key].append(description)
            if cde_key in by_key:
                continue
            by_key[cde_key] = CDEInfo(
                cde_id=_optional_int(item.get("cde_id")),
                cde_key=cde_key,
                description=description,
            )
        collapsed = tuple(
            CollapsedPlacement(
                cde_key=cde_key,
                node_count=len(descriptions),
                descriptions=tuple(dict.fromkeys(descriptions)),
            )
            for cde_key, descriptions in sorted(seen.items())
            if len(descriptions) > 1
        )
        return tuple(by_key.values()), collapsed

    def _load_values(self, path: str) -> dict[str, frozenset[str]]:
        """Pages because the service answers a too-large request with 413, not a short list."""
        collected: defaultdict[str, set[str]] = defaultdict(set)
        offset = 0
        while True:
            payload = self._get(path, {"limit": str(_PAGE_SIZE), "offset": str(offset)})
            items = payload.get("items", [])
            for item in items:
                cde_key = str(item.get("cde_key", "")).strip()
                value = str(item.get("pv_value", ""))
                if cde_key and value:
                    collected[cde_key].add(value)
            offset += len(items)
            if not items or offset >= int(payload.get("total_pvs", 0)):
                break
        return {key: frozenset(values) for key, values in collected.items()}

    def _label_for(self, model_key: str) -> str:
        for summary in self.list_models():
            if summary.data_model_key == model_key:
                return summary.label
        raise ReferenceModelNotFoundError(f"Unknown data model: {model_key}")

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = self._base_url + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            body = self._transport.get(
                url, {"x-api-key": self._api_key}, _TIMEOUT_SECONDS
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ReferenceModelNotFoundError(f"Not published: {path}") from exc
            raise ReferenceDataUnavailableError(
                f"Data Model Store returned HTTP {exc.code} for {path}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ReferenceDataUnavailableError("Data Model Store is unreachable") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ReferenceDataCorruptError(f"Data Model Store returned non-JSON for {path}") from exc
        if not isinstance(payload, dict):
            raise ReferenceDataCorruptError(f"Data Model Store returned a non-object for {path}")
        return payload


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        return None


__all__ = [
    "CollapsedPlacement",
    "DataModelStoreApiRepository",
    "HttpTransport",
    "UrllibTransport",
]
