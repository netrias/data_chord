from __future__ import annotations

import hashlib
from pathlib import Path

from scripts import reference_data
from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel
from src.integrations.reference_data_file import save_reference_models
from src.integrations.sqlite_reference_data import SqliteReferenceDataRepository


def test_load_sqlite_imports_and_reads_back_the_exact_approved_models(tmp_path: Path) -> None:
    # Given one approved canonical reference file and no portable database.
    source = _source_file(tmp_path)
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_model = reference_data.load_reference_models(source)[0]
    database = tmp_path / "standards.sqlite"
    assert database.exists() is False

    # When the operator loads that file into the portable reference store.
    reference_data._load_sqlite(source, expected_digest, database)

    # Then the normal repository returns the exact trusted domain model.
    assert SqliteReferenceDataRepository(database).load_model(expected_model.version) == expected_model


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
        catalog=CdeCatalog.from_cdes(
            [
                CDEInfo(None, "field", "Corrected", CdeType.PV),
            ]
        ),
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
        catalog=CdeCatalog.from_cdes([CDEInfo(None, "field", "Field", CdeType.PV)]),
        pvs=CdePvCatalog.from_mapping({"field": frozenset({"A", "B"})}),
    )
    source = tmp_path / "reference.json"
    save_reference_models(source, [model])
    return source
