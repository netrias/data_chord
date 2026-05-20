"""
Lazy-initialized service singletons shared across stages.

Axis of change: service wiring and lifecycle. Stages depend on getters, not constructors.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from netrias_client import Environment, NetriasClient

from src.domain.config import (
    ConfigurationError,
    get_netrias_api_key,
    get_netrias_timeout_seconds,
    get_storage_backend,
    get_upload_dir,
    get_workflow_s3_bucket,
    get_workflow_s3_prefix,
    get_workflow_storage_dir,
)
from src.domain.harmonize import HarmonizeService
from src.domain.mapping_service import MappingDiscoveryService
from src.domain.paths import PROJECT_ROOT
from src.domain.storage import (
    LocalWorkflowStorage,
    S3WorkflowClient,
    S3WorkflowStorage,
    UploadConstraints,
    UploadStorage,
    UserContext,
    WorkflowStorage,
)
from src.domain.user_context import current_user_context

logger = logging.getLogger(__name__)

UPLOAD_BASE_DIR = PROJECT_ROOT / "uploads"
DEFAULT_WORKFLOW_STORAGE_DIR = PROJECT_ROOT / "workflow_storage"
LOCAL_STORAGE_BACKEND = "local"
S3_STORAGE_BACKEND = "s3"
MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024

_upload_constraints: UploadConstraints | None = None
_storage: UploadStorage | None = None
_workflow_storage: WorkflowStorage | None = None
_mapping_discovery: MappingDiscoveryService | None = None
_harmonizer: HarmonizeService | None = None
_netrias_client: NetriasClient | None = None
_netrias_client_initialized: bool = False


def get_upload_constraints() -> UploadConstraints:
    global _upload_constraints  # noqa: PLW0603 - intentional singleton
    if _upload_constraints is None:
        _upload_constraints = UploadConstraints(max_bytes=MAX_UPLOAD_BYTES)
    return _upload_constraints


def get_upload_storage() -> UploadStorage:
    global _storage  # noqa: PLW0603 - intentional singleton
    if _storage is None:
        logger.info("Initializing upload storage")
        _storage = UploadStorage(_upload_base_dir(), get_upload_constraints())
    return _storage


def _upload_base_dir() -> Path:
    upload_dir = get_upload_dir()
    if upload_dir is None:
        return UPLOAD_BASE_DIR
    path = Path(upload_dir)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_workflow_storage() -> WorkflowStorage:
    global _workflow_storage  # noqa: PLW0603 - intentional singleton
    if _workflow_storage is None:
        backend = get_storage_backend()
        if backend == LOCAL_STORAGE_BACKEND:
            storage_dir = get_workflow_storage_dir()
            base_dir = DEFAULT_WORKFLOW_STORAGE_DIR if storage_dir is None else PROJECT_ROOT / storage_dir
            logger.info("Initializing local workflow storage", extra={"base_dir": str(base_dir)})
            _workflow_storage = LocalWorkflowStorage(base_dir)
        elif backend == S3_STORAGE_BACKEND:
            bucket = get_workflow_s3_bucket()
            if not bucket:
                raise ConfigurationError("DATA_CHORD_S3_BUCKET is required when DATA_CHORD_STORAGE=s3")
            import boto3

            _workflow_storage = S3WorkflowStorage(
                bucket=bucket,
                prefix=get_workflow_s3_prefix(),
                client=cast(S3WorkflowClient, boto3.client("s3")),
            )
        else:
            raise ConfigurationError(f"Unsupported DATA_CHORD_STORAGE value: {backend}")
    return _workflow_storage


def get_user_context() -> UserContext:
    return current_user_context()


def get_netrias_client() -> NetriasClient | None:
    """Why: None when NETRIAS_API_KEY missing — callers already guard with 'if not client'."""
    global _netrias_client, _netrias_client_initialized  # noqa: PLW0603
    if not _netrias_client_initialized:
        api_key = get_netrias_api_key()
        if api_key:
            timeout = get_netrias_timeout_seconds()
            try:
                _netrias_client = NetriasClient(api_key=api_key, environment=Environment.STAGING)
                if timeout is not None:
                    _netrias_client.configure(timeout=timeout)
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
    global _netrias_client, _netrias_client_initialized, _workflow_storage  # noqa: PLW0603
    _netrias_client = None
    _netrias_client_initialized = False
    _workflow_storage = None


__all__ = [
    "MAX_UPLOAD_BYTES",
    "UPLOAD_BASE_DIR",
    "DEFAULT_WORKFLOW_STORAGE_DIR",
    "cleanup_services",
    "get_harmonize_service",
    "get_mapping_service",
    "get_netrias_client",
    "get_upload_constraints",
    "get_upload_storage",
    "get_user_context",
    "get_workflow_storage",
]
