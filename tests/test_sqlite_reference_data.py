"""Feature tests for the portable SQLite standards boundary."""

from pathlib import Path

import pytest

from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel, ReferenceModelNotFoundError
from src.integrations.sqlite_reference_data import (
    SqliteReferenceDataImporter,
    SqliteReferenceDataRepository,
    SqliteReferenceImportConflictError,
)


def _model(version: str, *, description: str = "Values") -> ReferenceModel:
    return ReferenceModel(
        version=DataModelVersionReference("model", version),
        label="Model",
        catalog=CdeCatalog.from_cdes([
            CDEInfo(42, "valued", description, CdeType.PV),
            CDEInfo(None, "empty", None, CdeType.PASSTHROUGH),
        ]),
        pvs=CdePvCatalog.from_mapping({
            "valued": frozenset({"a", "b"}),
            "empty": frozenset(),
        }),
    )


def test_sqlite_repository_returns_complete_domain_models(tmp_path: Path) -> None:
    # Given two validated standard versions imported into one portable database.
    database = tmp_path / "standards.sqlite"
    SqliteReferenceDataImporter(database).import_models([_model("1"), _model("2")])

    # When the application reads through its normal reference-data boundary.
    repository = SqliteReferenceDataRepository(database)
    summaries = repository.list_models()
    loaded = repository.load_model(DataModelVersionReference("model", "2"))

    # Then SQLite rows become the same complete domain values used by every stage.
    assert summaries == (
        DataModelSummary(
            data_model_key="model",
            label="Model",
            versions=[DataModelVersionInfo("1"), DataModelVersionInfo("2")],
        ),
    )
    assert loaded == _model("2")
    assert loaded.catalog.get("valued") == CDEInfo(42, "valued", "Values", CdeType.PV)
    assert loaded.pvs.get("valued") == frozenset({"a", "b"})
    assert loaded.pvs.get("empty") == frozenset()


def test_sqlite_import_adds_versions_without_changing_published_versions(tmp_path: Path) -> None:
    # Given version 1 is already published and readable.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    importer.import_models([_model("1")])
    assert SqliteReferenceDataRepository(database).load_model(_model("1").version) == _model("1")

    # When a new external version is imported.
    importer.import_models([_model("2")])

    # Then both published versions remain readable.
    repository = SqliteReferenceDataRepository(database)
    assert repository.load_model(_model("1").version) == _model("1")
    assert repository.load_model(_model("2").version) == _model("2")


def test_sqlite_import_rejects_changed_content_for_a_published_version(tmp_path: Path) -> None:
    # Given one published external version.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    importer.import_models([_model("1")])

    # When an import reuses that identity with different content.
    with pytest.raises(SqliteReferenceImportConflictError, match="already published"):
        importer.import_models([_model("1", description="Changed")])

    # Then the original complete model remains the source of truth.
    assert SqliteReferenceDataRepository(database).load_model(_model("1").version) == _model("1")


def test_sqlite_import_explicitly_replaces_a_published_version(tmp_path: Path) -> None:
    # Given one published version and a corrected model with the same identity.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    importer.import_models([_model("1")])
    corrected = _model("1", description="Corrected")

    # When the operator explicitly permits replacement.
    importer.import_models([corrected], replace_existing=True)

    # Then every new read returns the corrected complete model.
    assert SqliteReferenceDataRepository(database).load_model(corrected.version) == corrected


def test_sqlite_import_relabels_all_replaced_versions_together(tmp_path: Path) -> None:
    # Given two published versions have the same old label.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    originals = [_model("1"), _model("2")]
    importer.import_models(originals)
    replacements = [
        ReferenceModel(
            version=model.version,
            label="Renamed model",
            catalog=model.catalog,
            pvs=model.pvs,
        )
        for model in originals
    ]

    # When the operator replaces every version in one batch.
    importer.import_models(replacements, replace_existing=True)

    # Then both versions use the new label and remain exact.
    repository = SqliteReferenceDataRepository(database)
    assert repository.list_models()[0].label == "Renamed model"
    assert [repository.load_model(model.version) for model in replacements] == replacements


def test_sqlite_import_cannot_relabel_only_some_stored_versions(tmp_path: Path) -> None:
    # Given two published versions have the same label.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    originals = [_model("1"), _model("2")]
    importer.import_models(originals)
    replacement = ReferenceModel(
        version=originals[0].version,
        label="Renamed model",
        catalog=originals[0].catalog,
        pvs=originals[0].pvs,
    )

    # When the operator tries to rename only one stored version.
    with pytest.raises(SqliteReferenceImportConflictError, match="label"):
        importer.import_models([replacement], replace_existing=True)

    # Then both original versions remain exact.
    repository = SqliteReferenceDataRepository(database)
    assert [repository.load_model(model.version) for model in originals] == originals


def test_failed_replacement_batch_restores_the_published_version(tmp_path: Path) -> None:
    # Given version 1 is published before a replacement batch with a later label conflict.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    original = _model("1")
    importer.import_models([original])
    corrected = _model("1", description="Corrected")
    new_version = _model("2")
    conflicting = ReferenceModel(
        version=new_version.version,
        label="Different label",
        catalog=new_version.catalog,
        pvs=new_version.pvs,
    )

    # When the later conflict fails the replacement transaction.
    with pytest.raises(SqliteReferenceImportConflictError, match="label"):
        importer.import_models([corrected, conflicting], replace_existing=True)

    # Then the original version remains exact and the new version is absent.
    repository = SqliteReferenceDataRepository(database)
    assert repository.load_model(original.version) == original
    with pytest.raises(ReferenceModelNotFoundError):
        repository.load_model(conflicting.version)


def test_reimporting_identical_models_is_safe(tmp_path: Path) -> None:
    # Given a standard version is already published.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    importer.import_models([_model("1")])

    # When the same validated model is imported again.
    importer.import_models([_model("1")])

    # Then one exact published model remains readable.
    repository = SqliteReferenceDataRepository(database)
    assert sum(len(summary.versions) for summary in repository.list_models()) == 1
    assert repository.load_model(_model("1").version) == _model("1")


def test_sqlite_repository_preserves_an_empty_permissible_value(tmp_path: Path) -> None:
    # Given a validated standard where the empty string is an explicit permissible value.
    model = _model("1")
    model = ReferenceModel(
        version=model.version,
        label=model.label,
        catalog=model.catalog,
        pvs=model.pvs.with_values({"valued": frozenset({"", "a"})}),
    )
    database = tmp_path / "standards.sqlite"

    # When the model crosses the SQLite write and read boundaries.
    SqliteReferenceDataImporter(database).import_models([model])
    loaded = SqliteReferenceDataRepository(database).load_model(model.version)

    # Then the domain value remains exact.
    assert loaded == model


def test_failed_batch_import_does_not_publish_earlier_models(tmp_path: Path) -> None:
    # Given version 1 is published and a batch contains version 2 before a conflicting version 1.
    database = tmp_path / "standards.sqlite"
    importer = SqliteReferenceDataImporter(database)
    importer.import_models([_model("1")])

    # When the later conflict fails the batch transaction.
    with pytest.raises(SqliteReferenceImportConflictError):
        importer.import_models([_model("2"), _model("1", description="Changed")])

    # Then version 2 was not partly published.
    repository = SqliteReferenceDataRepository(database)
    with pytest.raises(ReferenceModelNotFoundError):
        repository.load_model(_model("2").version)
    assert repository.load_model(_model("1").version) == _model("1")
