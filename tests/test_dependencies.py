"""Feature tests for app service wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agent_experiment import GPT_5_6_LUNA, ReasoningEffort
from cde_recommend.result_cache import DynamoRecommendationCache

import src.app.dependencies as dependencies
from src.integrations.agentic_harmonize import AgenticHarmonizeService
from src.integrations.bedrock_cde_ranker import BedrockCandidateRanker
from src.integrations.cde_recommendation import CdeRecommendationAdapter
from src.integrations.dynamodb_harmonization_cache import DynamoDbHarmonizationCache
from src.integrations.dynamodb_reference_data import DynamoDbReferenceDataRepository


def test_upload_storage_uses_configured_scratch_dir(monkeypatch, tmp_path: Path) -> None:
    # Given the hosted runtime points upload scratch at a writable directory.
    scratch_dir = tmp_path / "scratch"
    monkeypatch.setenv("DATA_CHORD_UPLOAD_DIR", str(scratch_dir))
    monkeypatch.setattr(dependencies, "_storage", None)

    # When upload storage is initialized through normal app wiring.
    storage = dependencies.get_upload_storage()

    # Then scratch directories are created under the configured location.
    assert storage is not None
    assert (scratch_dir / "files").is_dir()
    assert (scratch_dir / "meta").is_dir()


def test_reference_repository_uses_the_configured_table(monkeypatch) -> None:
    # Given a configured reference table and a fresh dependency container.
    monkeypatch.setenv("DATA_CHORD_REFERENCE_TABLE", "reference-table")
    monkeypatch.setattr(dependencies, "_reference_data_repository", None)
    table = MagicMock()
    resource = MagicMock()
    resource.Table.return_value = table
    boto3 = MagicMock()
    boto3.resource.return_value = resource
    monkeypatch.setitem(__import__("sys").modules, "boto3", boto3)

    # When the repository is initialized.
    repository = dependencies.get_reference_data_repository()

    # Then it uses the configured DynamoDB table.
    assert isinstance(repository, DynamoDbReferenceDataRepository)
    resource.Table.assert_called_once_with("reference-table")


def test_harmonizer_is_agentic_only(monkeypatch) -> None:
    # Given a fresh service container.
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("DATA_CHORD_HARMONIZATION_CACHE_TABLE", "cache-table")
    monkeypatch.setattr(dependencies, "_harmonize_service", None)
    cache = MagicMock()
    monkeypatch.setattr(dependencies, "get_harmonization_cache", lambda: cache)

    # When the harmonizer is loaded.
    service = dependencies.get_harmonize_service()

    # Then the in-task agentic harmonizer is the only implementation.
    assert isinstance(service, AgenticHarmonizeService)
    assert service._cache is cache  # noqa: SLF001 - verifies application wiring


def test_harmonization_cache_uses_the_configured_table(monkeypatch) -> None:
    # Given a configured cache table and a fresh dependency container.
    monkeypatch.setenv("DATA_CHORD_HARMONIZATION_CACHE_TABLE", "cache-table")
    monkeypatch.setattr(dependencies, "_harmonization_cache", None)
    table = MagicMock()
    resource = MagicMock()
    resource.Table.return_value = table
    boto3 = MagicMock()
    boto3.resource.return_value = resource
    monkeypatch.setitem(__import__("sys").modules, "boto3", boto3)

    # When the cache is initialized.
    cache = dependencies.get_harmonization_cache()

    # Then it uses the application-owned DynamoDB table.
    assert isinstance(cache, DynamoDbHarmonizationCache)
    resource.Table.assert_called_once_with("cache-table")


def test_cde_recommender_uses_bedrock_luna_and_the_owned_cache(monkeypatch) -> None:
    # Given the runtime has a region and an application-owned cache table.
    monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
    monkeypatch.setenv("DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE", "cde-cache-table")
    monkeypatch.setattr(dependencies, "_cde_recommender", None)

    # When the application builds its CDE recommender.
    service = dependencies.get_cde_recommender()

    # Then the one runtime path uses Bedrock Luna with medium reasoning and the named cache.
    assert isinstance(service, CdeRecommendationAdapter)
    assert isinstance(service._ranker, BedrockCandidateRanker)  # noqa: SLF001
    assert service._ranker._config.model == GPT_5_6_LUNA  # noqa: SLF001
    assert service._ranker._config.reasoning_effort == ReasoningEffort.MEDIUM  # noqa: SLF001
    assert isinstance(service._cache, DynamoRecommendationCache)  # noqa: SLF001
    assert service._cache._table_name == "cde-cache-table"  # noqa: SLF001
    assert service._cache._region == "us-gov-west-1"  # noqa: SLF001
