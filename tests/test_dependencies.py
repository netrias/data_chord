"""Feature tests for app service wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import src.app.dependencies as dependencies
from src.integrations.agentic_harmonize import AgenticHarmonizeService
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
