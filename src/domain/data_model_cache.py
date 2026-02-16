"""
Session-scoped cache for CDEs and permissible values.

CDEs and PVs don't change during a harmonization session, so caching avoids
repeated API calls. Each file_id gets its own cache.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from src.domain.cde import CDEInfo
from src.domain.config import get_data_model_key
from src.domain.data_model_client import DataModelClient, DataModelClientError

_logger = logging.getLogger(__name__)


@dataclass
class SessionCache:
    """Thread-safe for concurrent access during async operations."""

    # Data model metadata
    data_model_key: str = ""
    version_label: str = ""

    # CDE list (fetched in Stage 2)
    cdes: list[CDEInfo] = field(default_factory=list)
    cde_by_id: dict[int, CDEInfo] = field(default_factory=dict)
    cde_by_key: dict[str, CDEInfo] = field(default_factory=dict)

    # Column -> CDE mappings (set in Stage 2/3, used for PV lookup)
    column_to_cde_key: dict[str, str] = field(default_factory=dict)

    # PV sets keyed by cde_key (fetched in Stage 3)
    pvs: dict[str, frozenset[str]] = field(default_factory=dict)

    # Thread safety
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_cdes(self, cdes: list[CDEInfo], data_model_key: str, version_label: str) -> None:
        with self._lock:
            self.data_model_key = data_model_key
            self.version_label = version_label
            self.cdes = list(cdes)
            self.cde_by_id = {c.cde_id: c for c in cdes}
            self.cde_by_key = {c.cde_key: c for c in cdes}

    def get_cde_by_id(self, cde_id: int) -> CDEInfo | None:
        with self._lock:
            return self.cde_by_id.get(cde_id)

    def get_cde_by_key(self, cde_key: str) -> CDEInfo | None:
        with self._lock:
            return self.cde_by_key.get(cde_key)

    def get_all_cdes(self) -> list[CDEInfo]:
        with self._lock:
            return list(self.cdes)

    def has_cdes(self) -> bool:
        with self._lock:
            return len(self.cdes) > 0

    def set_column_mapping(self, column_name: str, cde_key: str) -> None:
        with self._lock:
            self.column_to_cde_key[column_name] = cde_key

    def set_column_mappings(self, mappings: dict[str, str]) -> None:
        """Full replacement prevents stale keys from previous mapping passes."""
        with self._lock:
            self.column_to_cde_key = dict(mappings)

    def get_column_cde_key(self, column_name: str) -> str | None:
        with self._lock:
            return self.column_to_cde_key.get(column_name)

    def get_column_mappings(self) -> dict[str, str]:
        """Thread-safe copy of column-to-CDE mappings for serialization."""
        with self._lock:
            return dict(self.column_to_cde_key)

    def set_pvs(self, cde_key: str, values: frozenset[str]) -> None:
        with self._lock:
            self.pvs[cde_key] = values

    def set_pvs_batch(self, pv_map: dict[str, frozenset[str]]) -> None:
        with self._lock:
            self.pvs.update(pv_map)

    def get_pvs_for_cde(self, cde_key: str) -> frozenset[str] | None:
        with self._lock:
            return self.pvs.get(cde_key)

    def get_pvs_for_column(self, column_name: str) -> frozenset[str] | None:
        with self._lock:
            cde_key = self.column_to_cde_key.get(column_name)
            if cde_key is None:
                return None
            return self.pvs.get(cde_key)

    def has_any_pvs(self) -> bool:
        with self._lock:
            return len(self.pvs) > 0

    def get_model_info(self) -> tuple[str, str]:
        with self._lock:
            return self.data_model_key, self.version_label


def populate_cde_cache(file_id: str, client: DataModelClient) -> None:
    """PV validation in Stage 3+ requires model key and version; must run before PV fetch."""
    data_model_key = get_data_model_key()
    try:
        version_label = client.get_latest_version(data_model_key)
    except DataModelClientError:
        _logger.warning("Data Model Store API unavailable; defaulting to version 1")
        version_label = "1"

    cdes = client.fetch_cdes(data_model_key, version_label)
    cache = get_session_cache(file_id)
    cache.set_cdes(cdes, data_model_key=data_model_key, version_label=version_label)

    _logger.info(
        "Populated CDE cache from Data Model Store API",
        extra={
            "file_id": file_id,
            "cde_count": len(cdes),
            "data_model": data_model_key,
            "version": version_label,
        },
    )


# Global session cache storage
_session_caches: dict[str, SessionCache] = {}
_global_lock = threading.Lock()


def get_session_cache(file_id: str) -> SessionCache:
    """Lazy initialization avoids pre-allocating caches for sessions that may never use PVs."""
    with _global_lock:
        if file_id not in _session_caches:
            _session_caches[file_id] = SessionCache()
        return _session_caches[file_id]


def clear_session_cache(file_id: str) -> None:
    """Prevents memory growth by releasing cache when session is complete."""
    with _global_lock:
        _session_caches.pop(file_id, None)


def clear_all_session_caches() -> None:
    """New uploads start fresh; stale PVs from previous sessions could cause incorrect validation."""
    with _global_lock:
        _session_caches.clear()


def has_session_cache(file_id: str) -> bool:
    with _global_lock:
        return file_id in _session_caches


__all__ = [
    "SessionCache",
    "populate_cde_cache",
    "get_session_cache",
    "clear_session_cache",
    "clear_all_session_caches",
    "has_session_cache",
]
