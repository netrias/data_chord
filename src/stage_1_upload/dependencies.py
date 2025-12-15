"""Share upload workflow service singletons across stages."""

from __future__ import annotations

import logging
from pathlib import Path

from src.domain.storage import FileStore, LocalStorageBackend

from .harmonize import HarmonizeService
from .mapping_service import MappingDiscoveryService
from .services import UploadConstraints, UploadStorage

logger = logging.getLogger(__name__)

MODULE_DIR = Path(__file__).parent
UPLOAD_BASE_DIR = MODULE_DIR / "uploads"

_upload_constraints = UploadConstraints(
    allowed_suffixes=(".csv",),
    allowed_content_types=("text/csv", "application/csv", "application/vnd.ms-excel"),
    max_bytes=25 * 1024 * 1024,
)
_storage = UploadStorage(UPLOAD_BASE_DIR, _upload_constraints)
_file_store: FileStore | None = None
_mapping_discovery: MappingDiscoveryService | None = None
_harmonizer: HarmonizeService | None = None


def get_upload_constraints() -> UploadConstraints:
    """why: expose the shared upload constraint configuration."""

    return _upload_constraints


def get_upload_storage() -> UploadStorage:
    """why: reuse the same storage workspace across stages."""

    return _storage


def get_mapping_service() -> MappingDiscoveryService:
    """why: lazily construct the mapping client without coupling routers."""

    global _mapping_discovery  # noqa: PLW0603 - intentional singleton
    if _mapping_discovery is None:
        logger.info("Initializing mapping discovery service")
        _mapping_discovery = MappingDiscoveryService()
    return _mapping_discovery


def get_harmonize_service() -> HarmonizeService:
    """why: reuse the harmonization client in every stage."""

    global _harmonizer  # noqa: PLW0603 - intentional singleton
    if _harmonizer is None:
        logger.info("Initializing harmonization service")
        _harmonizer = HarmonizeService()
    return _harmonizer


def get_file_store() -> FileStore:
    """why: provide typed file storage for all stages."""

    global _file_store  # noqa: PLW0603 - intentional singleton
    if _file_store is None:
        logger.info("Initializing file store")
        backend = LocalStorageBackend(UPLOAD_BASE_DIR / "manifests")
        _file_store = FileStore(backend)
    return _file_store


__all__ = [
    "MODULE_DIR",
    "UPLOAD_BASE_DIR",
    "get_upload_constraints",
    "get_upload_storage",
    "get_mapping_service",
    "get_harmonize_service",
    "get_file_store",
]
