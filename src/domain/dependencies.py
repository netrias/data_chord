"""
Provide lazy-initialized service singletons for the harmonization workflow.
"""

from __future__ import annotations

import logging

from netrias_client import Environment, NetriasClient

from src.domain.config import get_netrias_api_key
from src.domain.harmonize import HarmonizeService
from src.domain.mapping_service import MappingDiscoveryService
from src.domain.paths import PROJECT_ROOT
from src.domain.storage import FileStore, LocalStorageBackend, UploadConstraints, UploadStorage

logger = logging.getLogger(__name__)

UPLOAD_BASE_DIR = PROJECT_ROOT / "uploads"

MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
ALLOWED_SUFFIXES: tuple[str, ...] = (".csv",)
ALLOWED_CONTENT_TYPES: tuple[str, ...] = ("text/csv", "application/csv", "application/vnd.ms-excel")

_upload_constraints: UploadConstraints | None = None
_storage: UploadStorage | None = None
_file_store: FileStore | None = None
_mapping_discovery: MappingDiscoveryService | None = None
_harmonizer: HarmonizeService | None = None
_netrias_client: NetriasClient | None = None
_netrias_client_initialized: bool = False


def get_upload_constraints() -> UploadConstraints:
    global _upload_constraints  # noqa: PLW0603 - intentional singleton
    if _upload_constraints is None:
        _upload_constraints = UploadConstraints(
            allowed_suffixes=ALLOWED_SUFFIXES,
            allowed_content_types=ALLOWED_CONTENT_TYPES,
            max_bytes=MAX_UPLOAD_BYTES,
        )
    return _upload_constraints


def get_upload_storage() -> UploadStorage:
    global _storage  # noqa: PLW0603 - intentional singleton
    if _storage is None:
        logger.info("Initializing upload storage")
        _storage = UploadStorage(UPLOAD_BASE_DIR, get_upload_constraints())
    return _storage


def get_file_store() -> FileStore:
    global _file_store  # noqa: PLW0603 - intentional singleton
    if _file_store is None:
        logger.info("Initializing file store")
        backend = LocalStorageBackend(UPLOAD_BASE_DIR / "manifests")
        _file_store = FileStore(backend)
    return _file_store


def get_netrias_client() -> NetriasClient | None:
    """Why: None when NETRIAS_API_KEY missing — callers already guard with 'if not client'."""
    global _netrias_client, _netrias_client_initialized  # noqa: PLW0603
    if not _netrias_client_initialized:
        api_key = get_netrias_api_key()
        if api_key:
            try:
                _netrias_client = NetriasClient(api_key=api_key, environment=Environment.PROD)
            except Exception:
                logger.exception("Failed to initialize NetriasClient")
        else:
            logger.warning("NETRIAS_API_KEY missing; SDK calls will be unavailable.")
        _netrias_client_initialized = True
    return _netrias_client


def get_mapping_service() -> MappingDiscoveryService:
    global _mapping_discovery  # noqa: PLW0603 - intentional singleton
    if _mapping_discovery is None:
        logger.info("Initializing mapping discovery service")
        _mapping_discovery = MappingDiscoveryService(get_netrias_client())
    return _mapping_discovery


def get_harmonize_service() -> HarmonizeService:
    global _harmonizer  # noqa: PLW0603 - intentional singleton
    if _harmonizer is None:
        logger.info("Initializing harmonization service")
        _harmonizer = HarmonizeService(get_netrias_client())
    return _harmonizer


def cleanup_services() -> None:
    """Clean up resources held by singleton services (call on app shutdown)."""
    global _netrias_client, _netrias_client_initialized  # noqa: PLW0603
    _netrias_client = None
    _netrias_client_initialized = False


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_SUFFIXES",
    "MAX_UPLOAD_BYTES",
    "UPLOAD_BASE_DIR",
    "cleanup_services",
    "get_file_store",
    "get_harmonize_service",
    "get_mapping_service",
    "get_netrias_client",
    "get_upload_constraints",
    "get_upload_storage",
]
