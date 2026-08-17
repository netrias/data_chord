from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import reference_data
from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel
from src.integrations.reference_data_file import save_reference_models


class _Resource:
    def Table(self, table_name: str) -> object:  # noqa: N802 - boto3 framework name
        return table_name


class _Importer:
    imported: tuple[tuple[ReferenceModel, ...], str] | None = None

    def __init__(self, table: object) -> None:
        assert table == "reference-table"

    def import_models(
        self, models: Sequence[ReferenceModel], *, source_digest: str
    ) -> None:
        self.imported = (tuple(models), source_digest)
        type(self).imported = self.imported


class _Repository:
    models: tuple[ReferenceModel, ...] = ()

    def __init__(self, table: object) -> None:
        assert table == "reference-table"

    def load_model(self, version: DataModelVersionReference) -> ReferenceModel:
        return next(model for model in self.models if model.version == version)

    def list_models(self) -> tuple[DataModelSummary, ...]:
        model = self.models[0]
        return (
            DataModelSummary(
                model.version.data_model_key,
                model.label,
                [DataModelVersionInfo(model.version.external_version_number)],
            ),
        )


def test_sync_checks_the_source_hash_before_opening_dynamodb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a reference file that does not match its approved hash.
    source = _source_file(tmp_path)
    aws_opened = False

    def _resource(*_arguments: object, **_keywords: object) -> object:
        nonlocal aws_opened
        aws_opened = True
        return _Resource()

    monkeypatch.setattr(reference_data.boto3, "resource", _resource)

    # When synchronization starts, then it fails before DynamoDB is opened.
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        reference_data._sync(source, "0" * 64, "reference-table", "us-east-2")
    assert aws_opened is False


def test_sync_imports_and_reads_back_the_exact_approved_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given one approved canonical reference file and an empty target table.
    source = _source_file(tmp_path)
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_models = reference_data.load_reference_models(source)
    _Importer.imported = None
    _Repository.models = expected_models
    monkeypatch.setattr(reference_data.boto3, "resource", lambda *_args, **_kwargs: _Resource())
    monkeypatch.setattr(reference_data, "ReferenceDataImporter", _Importer)
    monkeypatch.setattr(reference_data, "DynamoDbReferenceDataRepository", _Repository)

    # When synchronization runs with the exact approved hash.
    reference_data._sync(source, expected_digest, "reference-table", "us-east-2")

    # Then the importer receives the exact models and source identity.
    assert _Importer.imported == (expected_models, expected_digest)


def _source_file(tmp_path: Path) -> Path:
    model = ReferenceModel(
        version=DataModelVersionReference("model", "1"),
        label="Model",
        catalog=CdeCatalog.from_cdes(
            [CDEInfo(None, "field", "Field", CdeType.PV)]
        ),
        pvs=CdePvCatalog.from_mapping({"field": frozenset({"A", "B"})}),
    )
    source = tmp_path / "reference.json"
    save_reference_models(source, [model])
    return source
