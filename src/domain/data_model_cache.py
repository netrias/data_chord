"""
Session-scoped cache for CDEs and permissible values.

CDEs and PVs don't change during a harmonization session, so caching avoids
repeated API calls. Each file_id gets its own cache.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from netrias_client import DataModelStoreError, NetriasAPIUnavailable

from src.domain.cde import CDEInfo
from src.domain.column_cde_map import ColumnCdeMap
from src.domain.column_profile import ColumnProfile
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.data_model_selection import DataModelSelection, version_number_from_label

_logger = logging.getLogger(__name__)


@dataclass
class SessionCache:
    """Thread-safe for concurrent access during async operations."""

    # Data model metadata
    data_model_selection: DataModelSelection | None = None

    # CDE list (fetched in Stage 2)
    cdes: list[CDEInfo] = field(default_factory=list)
    cde_by_id: dict[int, CDEInfo] = field(default_factory=dict)
    cde_by_key: dict[str, CDEInfo] = field(default_factory=dict)

    # Column -> CDE mappings (set in Stage 2/3, used for PV lookup)
    column_to_cde_key: ColumnCdeMap = field(default_factory=ColumnCdeMap.empty)

    # PV sets keyed by cde_key (fetched in Stage 3)
    pvs: dict[str, frozenset[str]] = field(default_factory=dict)

    # Per-column distinct-value profiles (computed in Stage 1 analyze, read by
    # the Stage 2 takeover via the column-detail endpoint).
    column_profiles: dict[ColumnKey, ColumnProfile] = field(default_factory=dict)

    # Thread safety
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_cdes(
        self,
        cdes: list[CDEInfo],
        data_model_key: str,
        version_label: str,
        version_number: int | None = None,
    ) -> None:
        with self._lock:
            selected_version_number = (
                version_number if version_number is not None else version_number_from_label(version_label)
            )
            self.data_model_selection = DataModelSelection(
                key=data_model_key,
                version_number=selected_version_number,
            )
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

    def set_column_mapping(self, column_key: ColumnKey | str, cde_key: str) -> None:
        with self._lock:
            mappings = self.column_to_cde_key.to_strings()
            mappings[str(column_key)] = cde_key
            self.column_to_cde_key = ColumnCdeMap.from_strings(mappings)

    def set_column_mappings(self, mappings: ColumnCdeMap | dict[str, str]) -> None:
        """Full replacement prevents stale keys from previous mapping passes."""
        with self._lock:
            if isinstance(mappings, ColumnCdeMap):
                self.column_to_cde_key = mappings
                return
            self.column_to_cde_key = ColumnCdeMap.from_strings(mappings)

    def get_column_cde_key(self, column_key: ColumnKey | str) -> str | None:
        with self._lock:
            return self.column_to_cde_key.mappings.get(column_key_from_string(str(column_key)))

    def get_column_mappings(self) -> ColumnCdeMap:
        """Thread-safe copy of column-to-CDE mappings for serialization."""
        with self._lock:
            return ColumnCdeMap(dict(self.column_to_cde_key.mappings))

    def set_pvs(self, cde_key: str, values: frozenset[str]) -> None:
        with self._lock:
            self.pvs[cde_key] = values

    def set_pvs_batch(self, pv_map: dict[str, frozenset[str]]) -> None:
        with self._lock:
            self.pvs.update(pv_map)

    def get_pvs_for_cde(self, cde_key: str) -> frozenset[str] | None:
        with self._lock:
            return self.pvs.get(cde_key)

    def get_pvs_for_column(self, column_key: ColumnKey | str) -> frozenset[str] | None:
        with self._lock:
            cde_key = self.column_to_cde_key.mappings.get(column_key_from_string(str(column_key)))
            if cde_key is None:
                return None
            return self.pvs.get(cde_key)

    def has_any_pvs(self) -> bool:
        with self._lock:
            return len(self.pvs) > 0

    def get_all_pvs(self) -> dict[str, frozenset[str]]:
        """Thread-safe snapshot of every cached PV set, keyed by cde_key."""
        with self._lock:
            return dict(self.pvs)

    def cde_keys_missing_pvs(self) -> list[str]:
        """Returns the cached CDE keys whose PV sets have not yet been fetched."""
        with self._lock:
            return [c.cde_key for c in self.cdes if c.cde_key not in self.pvs]

    def set_column_profiles(self, profiles: dict[str, ColumnProfile]) -> None:
        """Full replacement: a re-analyze always supersedes prior profiles."""
        with self._lock:
            self.column_profiles = {
                column_key_from_string(column_key): profile for column_key, profile in profiles.items()
            }

    def set_column_profile(self, profile: ColumnProfile) -> None:
        """Add or replace one profile, used when Stage 2 rebuilds after restart."""
        with self._lock:
            self.column_profiles[profile.column_key] = profile

    def get_column_profile(self, column_key: ColumnKey | str) -> ColumnProfile | None:
        with self._lock:
            return self.column_profiles.get(column_key_from_string(str(column_key)))

    def replace_cdes(self, cdes: list[CDEInfo]) -> None:
        """Swap the CDE list in place — used to apply post-PV-fetch type refinement."""
        with self._lock:
            self.cdes = list(cdes)
            self.cde_by_id = {c.cde_id: c for c in cdes}
            self.cde_by_key = {c.cde_key: c for c in cdes}

    def get_model_selection(self) -> DataModelSelection | None:
        with self._lock:
            return self.data_model_selection


def populate_cde_cache(file_id: str, selection: DataModelSelection) -> None:
    """PV validation in Stage 3+ requires model key and version; must run before PV fetch."""
    from src.domain.data_model_adapter import fetch_cdes, get_latest_version

    if selection.version_number is None:
        try:
            version_label = get_latest_version(selection.key)
        except (DataModelStoreError, NetriasAPIUnavailable):
            _logger.warning("Data Model Store API unavailable; defaulting to version 1")
            version_label = "1"
    else:
        version_label = selection.version_label

    cdes = fetch_cdes(selection.key, version_label)
    cache = get_session_cache(file_id)
    cache.set_cdes(
        cdes,
        data_model_key=selection.key,
        version_label=version_label,
        version_number=selection.version_number,
    )

    _logger.info(
        "Populated CDE cache from Data Model Store API",
        extra={
            "file_id": file_id,
            "cde_count": len(cdes),
            "data_model": selection.key,
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
