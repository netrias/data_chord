"""
Session-scoped cache for source-column profiles.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from src.domain.column_profile import ColumnProfile
from src.domain.columns import ColumnKey, column_key_from_string

_logger = logging.getLogger(__name__)
_LOCAL_CACHE_OWNER = "local-user"


@dataclass
class SessionCache:
    """Thread-safe for concurrent access during async operations."""

    # Per-column distinct-value profiles (computed in Stage 1 analyze, read by
    # the Stage 2 takeover via the column-detail endpoint).
    column_profiles: dict[ColumnKey, ColumnProfile] = field(default_factory=dict)

    # Thread safety
    _lock: threading.Lock = field(default_factory=threading.Lock)

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
    "get_session_cache",
    "clear_session_cache",
    "clear_all_session_caches",
    "has_session_cache",
]
