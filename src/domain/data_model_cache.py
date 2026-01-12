"""
Session-scoped cache for CDEs and permissible values.

why: Avoid repeated API calls; data doesn't change during a session.
Each file_id gets its own cache.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from src.domain.cde import CDEInfo


@dataclass
class SessionCache:
    """
    why: Store fetched CDEs and PVs for a harmonization session.

    Thread-safe for concurrent access during async operations.
    """

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
        """why: Cache fetched CDEs with lookup indexes."""
        with self._lock:
            self.data_model_key = data_model_key
            self.version_label = version_label
            self.cdes = list(cdes)
            self.cde_by_id = {c.cde_id: c for c in cdes}
            self.cde_by_key = {c.cde_key: c for c in cdes}

    def get_cde_by_id(self, cde_id: int) -> CDEInfo | None:
        """why: Lookup CDE by ID."""
        with self._lock:
            return self.cde_by_id.get(cde_id)

    def get_cde_by_key(self, cde_key: str) -> CDEInfo | None:
        """why: Lookup CDE by key."""
        with self._lock:
            return self.cde_by_key.get(cde_key)

    def get_all_cdes(self) -> list[CDEInfo]:
        """why: Return all CDEs for dropdown population."""
        with self._lock:
            return list(self.cdes)

    def has_cdes(self) -> bool:
        """why: Check if CDEs have been fetched."""
        with self._lock:
            return len(self.cdes) > 0

    def set_column_mapping(self, column_name: str, cde_key: str) -> None:
        """why: Track which CDE each column maps to for PV lookup."""
        with self._lock:
            self.column_to_cde_key[column_name] = cde_key

    def set_column_mappings(self, mappings: dict[str, str]) -> None:
        """why: Batch set column->CDE mappings."""
        with self._lock:
            self.column_to_cde_key.update(mappings)

    def get_column_cde_key(self, column_name: str) -> str | None:
        """why: Get the CDE key for a column."""
        with self._lock:
            return self.column_to_cde_key.get(column_name)

    def set_pvs(self, cde_key: str, values: frozenset[str]) -> None:
        """why: Cache PVs for a CDE."""
        with self._lock:
            self.pvs[cde_key] = values

    def set_pvs_batch(self, pv_map: dict[str, frozenset[str]]) -> None:
        """why: Batch cache PVs for multiple CDEs."""
        with self._lock:
            self.pvs.update(pv_map)

    def get_pvs_for_cde(self, cde_key: str) -> frozenset[str] | None:
        """why: Get cached PVs for a CDE."""
        with self._lock:
            return self.pvs.get(cde_key)

    def get_pvs_for_column(self, column_name: str) -> frozenset[str] | None:
        """why: Lookup PVs by column name (via column->CDE mapping)."""
        with self._lock:
            cde_key = self.column_to_cde_key.get(column_name)
            if cde_key is None:
                return None
            return self.pvs.get(cde_key)

    def has_any_pvs(self) -> bool:
        """why: Check if any PVs have been fetched (for validation gating)."""
        with self._lock:
            return len(self.pvs) > 0

    def get_model_info(self) -> tuple[str, str]:
        """why: Return (data_model_key, version_label) for API calls."""
        with self._lock:
            return self.data_model_key, self.version_label


# Global session cache storage
_session_caches: dict[str, SessionCache] = {}
_global_lock = threading.Lock()


def get_session_cache(file_id: str) -> SessionCache:
    """Get or create cache for a harmonization session."""
    with _global_lock:
        if file_id not in _session_caches:
            _session_caches[file_id] = SessionCache()
        return _session_caches[file_id]


def clear_session_cache(file_id: str) -> None:
    """Clean up cache when session ends (after Stage 5 download)."""
    with _global_lock:
        _session_caches.pop(file_id, None)


def has_session_cache(file_id: str) -> bool:
    """Check if cache exists for a file_id."""
    with _global_lock:
        return file_id in _session_caches
