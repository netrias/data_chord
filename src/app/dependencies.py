"""
Lazy-initialized service singletons shared across stages.

Axis of change: service wiring and lifecycle. Stages depend on getters, not constructors.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from src.app.harmonization_jobs import HarmonizationJobService
from src.app.harmonization_workflow import HarmonizationWorkflow
from src.auth.user_context import current_user_context
from src.cde_recommend.result_cache import DynamoRecommendationCache, EmptyRecommendationCache
from src.domain.cde_recommendation import CdeRecommender
from src.domain.harmonization_cache import EmptyHarmonizationCache, HarmonizationCache
from src.domain.reference_data import ReferenceDataError, ReferenceDataRepository
from src.integrations.agentic_harmonize import AgenticHarmonizeConfig, AgenticHarmonizeService
from src.integrations.bedrock_cde_ranker import (
    BedrockCandidateRanker,
    BedrockCandidateRankerConfig,
)
from src.integrations.cde_recommendation import CdeRecommendationAdapter
from src.integrations.demo_harmonization_cache import DemoHarmonizationCache
from src.integrations.dynamodb_harmonization_cache import DynamoDbHarmonizationCache
from src.integrations.dynamodb_reference_data import DynamoDbReferenceDataRepository, DynamoResource
from src.integrations.harmonize import HarmonizationWorkflowService, HarmonizeService
from src.integrations.sqlite_reference_data import SqliteReferenceDataRepository
from src.integrations.value_overlap_cde_recommendation import ValueOverlapCdeRecommender
from src.local_inference import (
    LocalInference,
    LocalInferenceError,
    LocalModelConfigurationError,
    load_local_inference,
)
from src.paths import PROJECT_ROOT
from src.settings import (
    ApplicationMode,
    ConfigurationError,
    HarmonizationMethod,
    RuntimeProfile,
    StorageBackend,
    get_agentic_workers,
    get_application_mode,
    get_aws_region,
    get_cde_recommendation_cache_table_name,
    get_data_dir,
    get_harmonization_cache_table_name,
    get_harmonization_method,
    get_max_active_jobs,
    get_reference_database_path,
    get_reference_table_name,
    get_runtime_profile,
    get_storage_backend,
    get_upload_dir,
    get_workflow_s3_bucket,
    get_workflow_s3_prefix,
    get_workflow_storage_dir,
    get_workflow_storage_limit_bytes,
)
from src.storage import (
    LocalWorkflowStorage,
    S3WorkflowClient,
    S3WorkflowStorage,
    UploadConstraints,
    UploadStorage,
    UserContext,
    WorkflowCleanup,
    WorkflowStorage,
)

logger = logging.getLogger(__name__)

UPLOAD_BASE_DIR = PROJECT_ROOT / "uploads"
DEFAULT_WORKFLOW_STORAGE_DIR = PROJECT_ROOT / "workflow_storage"
LOCAL_MODELS_CONFIG_PATH = PROJECT_ROOT / "config" / "local_models.json"
LOCAL_MODELS_ROOT = Path("/models")
MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
_UPLOAD_FREE_SPACE_RESERVE_BYTES = 4 * MAX_UPLOAD_BYTES

_upload_constraints: UploadConstraints | None = None
_storage: UploadStorage | None = None
_workflow_storage: WorkflowStorage | None = None
_workflow_cleanup: WorkflowCleanup | None = None
_reference_data_repository: ReferenceDataRepository | None = None
_harmonization_cache: HarmonizationCache | None = None
_harmonize_service: HarmonizeService | None = None


_harmonization_job_service: HarmonizationJobService | None = None
_cde_recommender: CdeRecommender | None = None
_local_inference: LocalInference | None = None


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
        if get_runtime_profile() is RuntimeProfile.PORTABLE:
            base_dir = get_data_dir()
            logger.info("Initializing portable workflow storage", extra={"base_dir": str(base_dir)})
            _workflow_storage = LocalWorkflowStorage(base_dir)
            return _workflow_storage
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


def get_workflow_cleanup() -> WorkflowCleanup | None:
    global _workflow_cleanup  # noqa: PLW0603 - intentional singleton
    if get_runtime_profile() is not RuntimeProfile.PORTABLE:
        return None
    if _workflow_cleanup is None:
        workflow_storage = get_workflow_storage()
        if not isinstance(workflow_storage, LocalWorkflowStorage):
            raise ConfigurationError("Portable workflow cleanup requires local workflow storage")
        _workflow_cleanup = WorkflowCleanup(
            workflow_storage,
            get_upload_storage(),
            capacity_bytes=get_workflow_storage_limit_bytes(),
            required_free_bytes=_UPLOAD_FREE_SPACE_RESERVE_BYTES,
        )
    return _workflow_cleanup


def get_user_context() -> UserContext:
    return current_user_context()


def validate_runtime_services() -> None:
    """Fail portable startup before health checks can report unusable local state."""
    if get_harmonization_method() is HarmonizationMethod.LOCAL:
        get_local_inference()
    if get_runtime_profile() is not RuntimeProfile.PORTABLE:
        return
    try:
        summaries = get_reference_data_repository().list_models()
    except ReferenceDataError as exc:
        raise ConfigurationError("Portable reference database is not usable") from exc
    if not any(summary.versions for summary in summaries):
        raise ConfigurationError("Portable reference database contains no model versions")

    try:
        workflow_storage = get_workflow_storage()
        if not isinstance(workflow_storage, LocalWorkflowStorage):
            raise ConfigurationError("Portable runtime requires local workflow storage")
        release_upload_lease = workflow_storage.acquire_upload_lease()
        release_upload_lease()
    except OSError as exc:
        raise ConfigurationError(f"Portable data directory is not writable: {get_data_dir()}") from exc


def get_reference_data_repository() -> ReferenceDataRepository:
    global _reference_data_repository  # noqa: PLW0603 - intentional singleton
    if _reference_data_repository is None:
        if get_runtime_profile() is RuntimeProfile.PORTABLE:
            _reference_data_repository = SqliteReferenceDataRepository(get_reference_database_path())
        else:
            import boto3

            resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=get_aws_region()))
            _reference_data_repository = DynamoDbReferenceDataRepository(resource.Table(get_reference_table_name()))
    return _reference_data_repository


def get_harmonization_cache() -> HarmonizationCache:
    global _harmonization_cache  # noqa: PLW0603 - intentional singleton
    if _harmonization_cache is None:
        if get_application_mode() is ApplicationMode.DEMO:
            _harmonization_cache = DemoHarmonizationCache()
        elif get_runtime_profile() is RuntimeProfile.PORTABLE:
            _harmonization_cache = EmptyHarmonizationCache()
        else:
            import boto3

            resource = cast(DynamoResource, boto3.resource("dynamodb", region_name=get_aws_region()))
            _harmonization_cache = DynamoDbHarmonizationCache(resource.Table(get_harmonization_cache_table_name()))
    return _harmonization_cache


def get_local_inference() -> LocalInference:
    global _local_inference  # noqa: PLW0603 - intentional singleton
    if _local_inference is None:
        try:
            _local_inference = load_local_inference(LOCAL_MODELS_CONFIG_PATH, LOCAL_MODELS_ROOT)
        except (LocalModelConfigurationError, LocalInferenceError) as exc:
            raise ConfigurationError(str(exc)) from exc
    return _local_inference


def get_harmonize_service() -> HarmonizeService:
    global _harmonize_service  # noqa: PLW0603 - intentional singleton
    if _harmonize_service is None:
        if get_harmonization_method() is HarmonizationMethod.LOCAL:
            _harmonize_service = HarmonizationWorkflowService(get_local_inference())
        else:
            _harmonize_service = AgenticHarmonizeService(
                AgenticHarmonizeConfig(
                    region=get_aws_region(),
                    max_workers=get_agentic_workers(),
                ),
                cache=get_harmonization_cache(),
            )
    return _harmonize_service


def get_harmonization_job_service() -> HarmonizationJobService:
    global _harmonization_job_service  # noqa: PLW0603 - intentional singleton
    if _harmonization_job_service is None:
        workflow = HarmonizationWorkflow(
            upload_storage=get_upload_storage(),
            workflow_storage=get_workflow_storage(),
            reference_data_repository=get_reference_data_repository(),
            harmonizer=get_harmonize_service(),
        )
        _harmonization_job_service = HarmonizationJobService(
            upload_storage=get_upload_storage(),
            workflow_storage=get_workflow_storage(),
            max_active_jobs=get_max_active_jobs(),
            workflow_runner=workflow.run,
        )
    return _harmonization_job_service


def get_cde_recommender() -> CdeRecommender:
    global _cde_recommender  # noqa: PLW0603 - intentional singleton
    if _cde_recommender is None:
        if get_application_mode() is ApplicationMode.DEMO:
            _cde_recommender = ValueOverlapCdeRecommender()
            return _cde_recommender
        region = get_aws_region()
        cache = (
            EmptyRecommendationCache()
            if get_runtime_profile() is RuntimeProfile.PORTABLE
            else DynamoRecommendationCache(
                get_cde_recommendation_cache_table_name(),
                region,
            )
        )
        _cde_recommender = CdeRecommendationAdapter(
            BedrockCandidateRanker(BedrockCandidateRankerConfig(region)),
            cache,
        )
    return _cde_recommender


def cleanup_services() -> None:
    """Clean up resources held by singleton services (call on app shutdown)."""
    global _cde_recommender, _harmonization_cache, _harmonize_service  # noqa: PLW0603
    global _local_inference  # noqa: PLW0603
    global _reference_data_repository, _workflow_cleanup, _workflow_storage  # noqa: PLW0603
    global _harmonization_job_service
    _cde_recommender = None
    _harmonization_cache = None
    _harmonize_service = None
    _local_inference = None
    _harmonization_job_service = None
    _reference_data_repository = None
    _workflow_cleanup = None
    _workflow_storage = None


async def shutdown_services() -> None:
    """Stop process-owned workers before singleton references are cleared."""
    await shutdown_harmonization_jobs()
    cleanup_services()


async def shutdown_harmonization_jobs() -> None:
    """Stop the current job service without creating one during shutdown."""
    if _harmonization_job_service is not None:
        await _harmonization_job_service.shutdown()


__all__ = [
    "MAX_UPLOAD_BYTES",
    "UPLOAD_BASE_DIR",
    "DEFAULT_WORKFLOW_STORAGE_DIR",
    "cleanup_services",
    "shutdown_services",
    "shutdown_harmonization_jobs",
    "get_harmonization_cache",
    "get_harmonize_service",
    "get_local_inference",
    "get_harmonization_job_service",
    "get_cde_recommender",
    "get_reference_data_repository",
    "get_upload_constraints",
    "get_upload_storage",
    "get_user_context",
    "get_workflow_storage",
    "get_workflow_cleanup",
    "validate_runtime_services",
]
