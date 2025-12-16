"""
Provide lazy-initialized service singletons for the harmonization workflow.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.domain.harmonize import HarmonizeService
from src.domain.mapping_service import MappingDiscoveryService
from src.domain.storage import FileStore, LocalStorageBackend, UploadConstraints, UploadStorage

logger = logging.getLogger(__name__)

MODULE_DIR: Path = Path(__file__).parent
UPLOAD_BASE_DIR: Path = MODULE_DIR.parent / "stage_1_upload" / "uploads"

MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
ALLOWED_SUFFIXES: tuple[str, ...] = (".csv",)
ALLOWED_CONTENT_TYPES: tuple[str, ...] = ("text/csv", "application/csv", "application/vnd.ms-excel")

_upload_constraints: UploadConstraints | None = None
_storage: UploadStorage | None = None
_file_store: FileStore | None = None
_mapping_discovery: MappingDiscoveryService | None = None
_harmonizer: HarmonizeService | None = None


def get_upload_constraints() -> UploadConstraints:
    """why: expose the shared upload constraint configuration."""

    global _upload_constraints  # noqa: PLW0603 - intentional singleton
    if _upload_constraints is None:
        _upload_constraints = UploadConstraints(
            allowed_suffixes=ALLOWED_SUFFIXES,
            allowed_content_types=ALLOWED_CONTENT_TYPES,
            max_bytes=MAX_UPLOAD_BYTES,
        )
    return _upload_constraints


def get_upload_storage() -> UploadStorage:
    """why: reuse the same storage workspace across stages."""

    global _storage  # noqa: PLW0603 - intentional singleton
    if _storage is None:
        logger.info("Initializing upload storage")
        _storage = UploadStorage(UPLOAD_BASE_DIR, get_upload_constraints())
    return _storage


def get_file_store() -> FileStore:
    """why: provide typed file storage for all stages."""

    global _file_store  # noqa: PLW0603 - intentional singleton
    if _file_store is None:
        logger.info("Initializing file store")
        backend = LocalStorageBackend(UPLOAD_BASE_DIR / "manifests")
        _file_store = FileStore(backend)
    return _file_store


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


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_SUFFIXES",
    "MAX_UPLOAD_BYTES",
    "MODULE_DIR",
    "UPLOAD_BASE_DIR",
    "get_file_store",
    "get_harmonize_service",
    "get_mapping_service",
    "get_upload_constraints",
    "get_upload_storage",
]
