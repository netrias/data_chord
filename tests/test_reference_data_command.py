from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import reference_data
from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel
from src.domain.reference_placements import CdePlacement, PlacedReferenceModel
from src.integrations.reference_data_file import (
    load_placed_reference_models,
    save_placed_reference_models,
    save_reference_models,
)
from src.integrations.sqlite_reference_data import SqliteReferenceDataRepository


class _Resource:
    def Table(self, table_name: str) -> object:  # noqa: N802 - boto3 framework name
        return table_name


class _Importer:
    imported: tuple[tuple[PlacedReferenceModel, ...], str] | None = None

    def __init__(self, table: object) -> None:
        assert table == "reference-table"

    def import_models(
        self, models: Sequence[PlacedReferenceModel], *, source_digest: str
    ) -> None:
        self.imported = (tuple(models), source_digest)
        type(self).imported = self.imported


class _Repository:
    models: tuple[PlacedReferenceModel, ...] = ()

    def __init__(self, table: object) -> None:
        assert table == "reference-table"

    def load_placements(self, version: DataModelVersionReference) -> PlacedReferenceModel:
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
    source = _placed_source_file(tmp_path)
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
    source = _placed_source_file(tmp_path)
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_models = load_placed_reference_models(source)
    _Importer.imported = None
    _Repository.models = expected_models
    monkeypatch.setattr(reference_data.boto3, "resource", lambda *_args, **_kwargs: _Resource())
    monkeypatch.setattr(reference_data, "ReferenceDataImporter", _Importer)
    monkeypatch.setattr(reference_data, "DynamoDbReferenceDataRepository", _Repository)

    # When synchronization runs with the exact approved hash.
    reference_data._sync(source, expected_digest, "reference-table", "us-east-2")

    # Then the importer receives the exact models and source identity.
    assert _Importer.imported == (expected_models, expected_digest)


def test_export_writes_nodes_and_placements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_model = load_placed_reference_models(_placed_source_file(tmp_path))[0]
    class _Client:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def configure(self, *, data_model_store_url: str) -> None:
            assert data_model_store_url == "https://poc.example/staging"

    monkeypatch.setattr(reference_data, "NetriasClient", _Client)
    monkeypatch.setattr(reference_data, "list_data_model_summaries", lambda _client: [
        DataModelSummary("model", "Model", [DataModelVersionInfo("1")])
    ])

    async def nodes_and_placements(*_args: object) -> tuple[tuple[str, ...], tuple[CdePlacement, ...]]:
        return source_model.nodes, source_model.placements

    async def all_pvs(*_args: object) -> CdePvCatalog:
        return CdePvCatalog.from_mapping(dict(source_model.values))

    monkeypatch.setattr(reference_data, "fetch_nodes_and_placements_async", nodes_and_placements)
    monkeypatch.setattr(reference_data, "fetch_all_pvs_async", all_pvs)
    exported = tmp_path / "exported.json"
    asyncio.run(reference_data._export("https://poc.example/staging", "test-key", exported))
    assert load_placed_reference_models(exported) == (source_model,)


def test_export_command_reads_poc_settings_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'DATA_CHORD_DMS_URL="https://poc.example/staging"\nDATA_CHORD_DMS_API_KEY="test-key"\n'
    )
    output = tmp_path / "export.json"
    selected: list[tuple[str, str, Path]] = []

    async def fake_export(url: str, key: str, path: Path) -> None:
        selected.append((url, key, path))

    monkeypatch.setattr(reference_data, "_export", fake_export)
    monkeypatch.delenv("DATA_CHORD_DMS_URL", raising=False)
    monkeypatch.delenv("DATA_CHORD_DMS_API_KEY", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["reference_data", "export", "--env-file", str(env_file), "--output", str(output)]
    )
    reference_data.main()
    assert selected == [("https://poc.example/staging", "test-key", output)]


def test_export_command_does_not_fall_back_to_legacy_dms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATA_CHORD_DMS_URL", raising=False)
    monkeypatch.delenv("DATA_CHORD_DMS_API_KEY", raising=False)
    monkeypatch.setenv("NETRIAS_API_KEY", "legacy-key")
    monkeypatch.setattr(
        sys, "argv", ["reference_data", "export", "--env-file", str(tmp_path / "missing"),
                      "--output", str(tmp_path / "export.json")]
    )
    with pytest.raises(SystemExit, match="2"):
        reference_data.main()


def test_load_sqlite_imports_and_reads_back_the_exact_approved_models(tmp_path: Path) -> None:
    # Given one approved canonical reference file and no portable database.
    source = _source_file(tmp_path)
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    database = tmp_path / "standards.sqlite"
    assert database.exists() is False

    # When the operator loads that file into the portable reference store.
    reference_data._load_sqlite(source, expected_digest, database)

    # Then the normal repository returns the exact trusted domain model.
    expected_model = reference_data.load_reference_models(source)[0]
    assert SqliteReferenceDataRepository(database).load_model(expected_model.version) == expected_model


def test_load_sqlite_accepts_placed_export(tmp_path: Path) -> None:
    source = _placed_source_file(tmp_path)
    database = tmp_path / "standards.sqlite"
    reference_data._load_sqlite(source, hashlib.sha256(source.read_bytes()).hexdigest(), database)
    placed = load_placed_reference_models(source)[0]
    assert SqliteReferenceDataRepository(database).load_model(placed.version) == placed.collapse()


def test_load_sqlite_replaces_changed_content_only_when_explicit(tmp_path: Path) -> None:
    # Given a portable database and an approved correction with the same model identity.
    source = _source_file(tmp_path)
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    database = tmp_path / "standards.sqlite"
    reference_data._load_sqlite(source, expected_digest, database)
    corrected = reference_data.load_reference_models(source)[0]
    corrected = ReferenceModel(
        version=corrected.version,
        label=corrected.label,
        catalog=CdeCatalog.from_cdes([
            CDEInfo(None, "field", "Corrected", CdeType.PV),
        ]),
        pvs=corrected.pvs,
    )
    corrected_source = tmp_path / "corrected-reference.json"
    save_reference_models(corrected_source, [corrected])
    corrected_digest = hashlib.sha256(corrected_source.read_bytes()).hexdigest()

    # When the operator loads the correction with explicit replacement.
    reference_data._load_sqlite(
        corrected_source,
        corrected_digest,
        database,
        replace_existing=True,
    )

    # Then the normal repository returns the approved correction.
    assert SqliteReferenceDataRepository(database).load_model(corrected.version) == corrected


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


def _placed_source_file(tmp_path: Path) -> Path:
    model = PlacedReferenceModel(
        version=DataModelVersionReference("model", "1"), label="Model",
        nodes=("diagnosis", "sample", "unused"),
        placements=(
            CdePlacement("diagnosis", "stage", 2, "Stage", "string", True),
            CdePlacement("sample", "stage", 7, "Stage", "enum", False),
        ),
        values={"stage": frozenset({"I", "II"})},
    )
    source = tmp_path / "placed-reference.json"
    save_placed_reference_models(source, [model])
    return source
