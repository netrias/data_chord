import hashlib
import json
from pathlib import Path

import pytest

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceDataCorruptError, ReferenceModel
from src.integrations.reference_data_file import (
    FileReferenceDataRepository,
    load_reference_models,
    save_reference_models,
)


def _model() -> ReferenceModel:
    return ReferenceModel(
        version=DataModelVersionReference("model", "1"),
        label="Model",
        catalog=CdeCatalog.from_cdes([
            CDEInfo(42, "valued", "Values", CdeType.PV),
            CDEInfo(None, "empty", None, CdeType.PASSTHROUGH),
        ]),
        pvs=CdePvCatalog.from_mapping({"valued": frozenset({"b", "a"}), "empty": frozenset()}),
    )


def test_canonical_export_round_trips_exact_reference_data(tmp_path: Path) -> None:
    # Given one complete reference model.
    path = tmp_path / "reference.json"

    # When it is exported and loaded again.
    save_reference_models(path, [_model()])
    loaded = load_reference_models(path)

    # Then the model and explicit empty value set are exact and the file is stable.
    assert loaded == (_model(),)
    assert loaded[0].catalog.get("valued") == CDEInfo(42, "valued", "Values", CdeType.PV)
    first_bytes = path.read_bytes()
    save_reference_models(path, [_model()])
    assert path.read_bytes() == first_bytes


def test_file_repository_lists_and_loads_canonical_models(tmp_path: Path) -> None:
    # Given one canonical reference file with a complete model.
    path = tmp_path / "reference.json"
    save_reference_models(path, [_model()])

    # When a local process opens the file as its reference repository.
    repository = FileReferenceDataRepository(path)

    # Then it lists and loads the same complete model without an external service.
    assert repository.list_models()[0].data_model_key == "model"
    assert repository.load_model(_model().version) == _model()


def test_canonical_export_rejects_duplicate_model_versions(tmp_path: Path) -> None:
    # Given a file with the same model identity twice.
    path = tmp_path / "duplicate.json"
    save_reference_models(path, [_model()])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["models"].append(payload["models"][0])
    payload["model_count"] = 2
    encoded = json.dumps(payload["models"], ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    payload["digest"] = hashlib.sha256(encoded).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")

    # When it is loaded, then ambiguous migration input is rejected.
    with pytest.raises(ReferenceDataCorruptError, match="duplicate model"):
        load_reference_models(path)


def test_canonical_export_rejects_tampered_content(tmp_path: Path) -> None:
    # Given a valid recovery file whose model content is changed without updating its digest.
    path = tmp_path / "tampered.json"
    save_reference_models(path, [_model()])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["models"][0]["label"] = "Changed"
    path.write_text(json.dumps(payload), encoding="utf-8")

    # When it is loaded, then the aggregate integrity check rejects it.
    with pytest.raises(ReferenceDataCorruptError, match="integrity"):
        load_reference_models(path)
