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
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_profile import ColumnProfile
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.data_model_version_reference import DataModelVersionReference

_logger = logging.getLogger(__name__)
_LOCAL_CACHE_OWNER = "local-user"


class ReferenceDataVersionMismatchError(Exception):
    """Raised when reference facts are added to a cache for another model version."""


@dataclass
class SessionCache:
    """Thread-safe for concurrent access during async operations."""

    # Data model metadata
    data_model_version: DataModelVersionReference | None = None

    # CDE list (fetched in Stage 2)
    cde_catalog: CdeCatalog = field(default_factory=CdeCatalog.empty)

    # PV sets keyed by cde_key (fetched in Stage 3)
    pvs: CdePvCatalog = field(default_factory=CdePvCatalog.empty)

    # Per-column distinct-value profiles (computed in Stage 1 analyze, read by
    # the Stage 2 takeover via the column-detail endpoint).
    column_profiles: dict[ColumnKey, ColumnProfile] = field(default_factory=dict)

    # Thread safety
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_cdes(
        self,
        cdes: list[CDEInfo],
        data_model_key: str,
        external_version_number: str,
    ) -> None:
        self.set_cde_catalog(
            CdeCatalog.from_cdes(cdes),
            data_model_key=data_model_key,
            external_version_number=external_version_number,
        )

    def set_cde_catalog(
        self,
        catalog: CdeCatalog,
        data_model_key: str,
        external_version_number: str,
    ) -> None:
        version = DataModelVersionReference(
            data_model_key=data_model_key,
            external_version_number=external_version_number,
        )
        with self._lock:
            if self.data_model_version != version:
                self.pvs = CdePvCatalog.empty()
            self.data_model_version = version
            self.cde_catalog = catalog

    def install_reference_data(
        self,
        data_model_version: DataModelVersionReference,
        cde_catalog: CdeCatalog,
        pvs: CdePvCatalog,
    ) -> None:
        """Atomically replace all model-version-scoped reference facts."""
        with self._lock:
            self.data_model_version = data_model_version
            self.cde_catalog = cde_catalog
            self.pvs = pvs

    def get_cde_by_key(self, cde_key: str) -> CDEInfo | None:
        with self._lock:
            return self.cde_catalog.get(cde_key)

    def get_all_cdes(self) -> list[CDEInfo]:
        with self._lock:
            return self.cde_catalog.to_list()

    def get_cde_catalog(self) -> CdeCatalog:
        with self._lock:
            return self.cde_catalog

    def has_cdes(self) -> bool:
        with self._lock:
            return not self.cde_catalog.is_empty()

    def set_pvs_batch(
        self,
        pv_map: CdePvCatalog,
        *,
        expected_version: DataModelVersionReference,
    ) -> None:
        with self._lock:
            if self.data_model_version != expected_version:
                raise ReferenceDataVersionMismatchError(
                    f"Reference data changed while fetching PVs for {expected_version.data_model_key}"
                )
            self.pvs = self.pvs.with_values(pv_map.values)

    def has_any_pvs(self) -> bool:
        with self._lock:
            return self.pvs.has_any()

    def get_all_pvs(self) -> CdePvCatalog:
        """Thread-safe snapshot of every cached PV set, keyed by cde_key."""
        with self._lock:
            return CdePvCatalog.from_mapping(self.pvs.values)

    def cde_keys_missing_pvs(self) -> list[str]:
        """Returns the cached CDE keys whose PV sets have not yet been fetched."""
        with self._lock:
            return self.pvs.missing_for(self.cde_catalog)

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

    def replace_cde_catalog(self, catalog: CdeCatalog) -> None:
        """Swap the CDE catalog in place after post-PV-fetch type refinement."""
        with self._lock:
            self.cde_catalog = catalog

    def get_data_model_version(self) -> DataModelVersionReference | None:
        with self._lock:
            return self.data_model_version


# Global session cache storage
_session_caches: dict[tuple[str, str], SessionCache] = {}
_global_lock = threading.Lock()


def get_session_cache(file_id: str, *, owner_user_id: str = _LOCAL_CACHE_OWNER) -> SessionCache:
    """Lazy initialization avoids pre-allocating caches for sessions that may never use PVs."""
    cache_key = (owner_user_id, file_id)
    with _global_lock:
        if cache_key not in _session_caches:
            _session_caches[cache_key] = SessionCache()
        return _session_caches[cache_key]


def clear_session_cache(file_id: str, *, owner_user_id: str | None = None) -> None:
    """Prevents memory growth by releasing cache when session is complete."""
    with _global_lock:
        if owner_user_id is not None:
            _session_caches.pop((owner_user_id, file_id), None)
            return
        for cache_key in [key for key in _session_caches if key[1] == file_id]:
            _session_caches.pop(cache_key, None)


def clear_all_session_caches() -> None:
    """New uploads start fresh; stale PVs from previous sessions could cause incorrect validation."""
    with _global_lock:
        _session_caches.clear()


def has_session_cache(file_id: str, *, owner_user_id: str | None = None) -> bool:
    with _global_lock:
        if owner_user_id is not None:
            return (owner_user_id, file_id) in _session_caches
        return any(cache_file_id == file_id for _, cache_file_id in _session_caches)


__all__ = [
    "SessionCache",
    "ReferenceDataVersionMismatchError",
    "get_session_cache",
    "clear_session_cache",
    "clear_all_session_caches",
    "has_session_cache",
]
