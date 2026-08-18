"""
Lazy-initialized service singletons shared across stages.

Axis of change: service wiring and lifecycle. Stages depend on getters, not constructors.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from src.auth.user_context import current_user_context
from src.cde_recommend.result_cache import DynamoRecommendationCache
from src.domain.cde_recommendation import CdeRecommender
from src.domain.harmonization_cache import HarmonizationCache
from src.domain.reference_data import ReferenceDataRepository
from src.integrations.agentic_harmonize import AgenticHarmonizeConfig, AgenticHarmonizeService
from src.integrations.bedrock_cde_ranker import (
    BedrockCandidateRanker,
    BedrockCandidateRankerConfig,
)
from src.integrations.cde_recommendation import CdeRecommendationAdapter
from src.integrations.dynamodb_harmonization_cache import DynamoDbHarmonizationCache
from src.integrations.dynamodb_reference_data import DynamoDbReferenceDataRepository, DynamoResource
from src.integrations.harmonize import HarmonizeService
from src.paths import PROJECT_ROOT
from src.settings import (
    ConfigurationError,
    StorageBackend,
    get_agentic_workers,
    get_aws_region,
    get_cde_recommendation_cache_table_name,
    get_harmonization_cache_table_name,
    get_reference_table_name,
    get_storage_backend,
    get_upload_dir,
    get_workflow_s3_bucket,
    get_workflow_s3_prefix,
    get_workflow_storage_dir,
)
from src.storage import (
    LocalWorkflowStorage,
    S3WorkflowClient,
    S3WorkflowStorage,
    UploadConstraints,
    UploadStorage,
    UserContext,
    WorkflowStorage,
)

logger = logging.getLogger(__name__)

UPLOAD_BASE_DIR = PROJECT_ROOT / "uploads"
DEFAULT_WORKFLOW_STORAGE_DIR = PROJECT_ROOT / "workflow_storage"
MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024

_upload_constraints: UploadConstraints | None = None
_storage: UploadStorage | None = None
_workflow_storage: WorkflowStorage | None = None
_reference_data_repository: ReferenceDataRepository | None = None
_harmonization_cache: HarmonizationCache | None = None
_harmonize_service: HarmonizeService | None = None
_cde_recommender: CdeRecommender | None = None


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
        if backend == StorageBackend.LOCAL:
            storage_dir = get_workflow_storage_dir()
            base_dir = DEFAULT_WORKFLOW_STORAGE_DIR if storage_dir is None else PROJECT_ROOT / storage_dir
            logger.info("Initializing local workflow storage", extra={"base_dir": str(base_dir)})
            _workflow_storage = LocalWorkflowStorage(base_dir)
        elif backend == StorageBackend.S3:
            bucket = get_workflow_s3_bucket()
            if not bucket:
                raise ConfigurationError("DATA_CHORD_S3_BUCKET is required when DATA_CHORD_STORAGE=s3")
            # Lazy import keeps local storage and lightweight tests from initializing AWS clients.
            import boto3

            _workflow_storage = S3WorkflowStorage(
                bucket=bucket,
                prefix=get_workflow_s3_prefix(),
                client=cast(S3WorkflowClient, boto3.client("s3")),
            )
        else:
            raise ConfigurationError(f"Unsupported DATA_CHORD_STORAGE value: {backend.value}")
    return _workflow_storage


def get_user_context() -> UserContext:
    return current_user_context()


def get_reference_data_repository() -> ReferenceDataRepository:
    global _reference_data_repository  # noqa: PLW0603 - intentional singleton
    if _reference_data_repository is None:
        import boto3

        resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=get_aws_region()))
        _reference_data_repository = DynamoDbReferenceDataRepository(resource.Table(get_reference_table_name()))
    return _reference_data_repository


def get_harmonization_cache() -> HarmonizationCache:
    global _harmonization_cache  # noqa: PLW0603 - intentional singleton
    if _harmonization_cache is None:
        import boto3

        resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=get_aws_region()))
        _harmonization_cache = DynamoDbHarmonizationCache(
            resource.Table(get_harmonization_cache_table_name())
        )
    return _harmonization_cache


def get_harmonize_service() -> HarmonizeService:
    global _harmonize_service  # noqa: PLW0603 - intentional singleton
    if _harmonize_service is None:
        _harmonize_service = AgenticHarmonizeService(
            AgenticHarmonizeConfig(
                region=get_aws_region(),
                max_workers=get_agentic_workers(),
            ),
            cache=get_harmonization_cache(),
        )
    return _harmonize_service


def get_cde_recommender() -> CdeRecommender:
    global _cde_recommender  # noqa: PLW0603 - intentional singleton
    if _cde_recommender is None:
        region = get_aws_region()
        _cde_recommender = CdeRecommendationAdapter(
            BedrockCandidateRanker(BedrockCandidateRankerConfig(region)),
            DynamoRecommendationCache(
                get_cde_recommendation_cache_table_name(),
                region,
            ),
        )
    return _cde_recommender


def cleanup_services() -> None:
    """Clean up resources held by singleton services (call on app shutdown)."""
    global _cde_recommender, _harmonization_cache, _harmonize_service  # noqa: PLW0603
    global _reference_data_repository, _workflow_storage  # noqa: PLW0603
    _cde_recommender = None
    _harmonization_cache = None
    _harmonize_service = None
    _reference_data_repository = None
    _workflow_storage = None


__all__ = [
    "MAX_UPLOAD_BYTES",
    "UPLOAD_BASE_DIR",
    "DEFAULT_WORKFLOW_STORAGE_DIR",
    "cleanup_services",
    "get_harmonization_cache",
    "get_harmonize_service",
    "get_cde_recommender",
    "get_reference_data_repository",
    "get_upload_constraints",
    "get_upload_storage",
    "get_user_context",
    "get_workflow_storage",
]
