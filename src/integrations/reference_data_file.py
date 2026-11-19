"""Canonical recovery file for reference-data migration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceModel,
    ReferenceModelNotFoundError,
)

FILE_SCHEMA_VERSION = 1


class FileReferenceDataRepository:
    """Read-only reference repository for local and browser-test processes."""

    def __init__(self, path: Path) -> None:
        models = load_reference_models(path)
        self._models = {model.version: model for model in models}
        self._summaries = _summaries(models)

    def list_models(self) -> tuple[DataModelSummary, ...]:
        return self._summaries

    def load_model(self, version: DataModelVersionReference) -> ReferenceModel:
        try:
            return self._models[version]
        except KeyError as exc:
            raise ReferenceModelNotFoundError(
                f"Reference model is not published: {version.data_model_key}/{version.external_version_number}"
            ) from exc


def save_reference_models(path: Path, models: Sequence[ReferenceModel]) -> None:
    """Write stable JSON that can rebuild any target environment."""
    model_payloads = [_model_to_payload(model) for model in sorted(models, key=_model_order)]
    payload = {
        "schema_version": FILE_SCHEMA_VERSION,
        "model_count": len(model_payloads),
        "digest": _digest(model_payloads),
        "models": model_payloads,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_reference_models(path: Path) -> tuple[ReferenceModel, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceDataCorruptError("Reference export file is unreadable") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != FILE_SCHEMA_VERSION:
        raise ReferenceDataCorruptError("Reference export schema is unsupported")
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise ReferenceDataCorruptError("Reference export models must be a list")
    if payload.get("model_count") != len(raw_models) or payload.get("digest") != _digest(raw_models):
        raise ReferenceDataCorruptError("Reference export integrity check failed")
    models = tuple(_model_from_payload(raw) for raw in raw_models)
    identities = {(model.version.data_model_key, model.version.external_version_number) for model in models}
    if len(identities) != len(models):
        raise ReferenceDataCorruptError("Reference export contains duplicate model versions")
    return models


def _model_to_payload(model: ReferenceModel) -> Mapping[str, object]:
    return {
        "data_model_key": model.version.data_model_key,
        "external_version_number": model.version.external_version_number,
        "label": model.label,
        "cdes": [
            {
                "cde_id": cde.cde_id,
                "cde_key": cde.cde_key,
                "description": cde.description,
                "cde_type": cde.cde_type.value,
                "values": sorted(model.pvs.get(cde.cde_key) or ()),
            }
            for cde in sorted(model.catalog, key=lambda item: item.cde_key)
        ],
    }


def _model_from_payload(raw: object) -> ReferenceModel:
    if not isinstance(raw, Mapping):
        raise ReferenceDataCorruptError("Reference export model must be an object")
    key = _string(raw, "data_model_key")
    version = _string(raw, "external_version_number")
    label = _string(raw, "label")
    raw_cdes = raw.get("cdes")
    if not isinstance(raw_cdes, list):
        raise ReferenceDataCorruptError("Reference export CDEs must be a list")
    cdes: list[CDEInfo] = []
    pvs: dict[str, frozenset[str]] = {}
    for raw_cde in raw_cdes:
        cde, values = _cde_from_payload(raw_cde)
        cde_key = cde.cde_key
        if cde_key in pvs:
            raise ReferenceDataCorruptError(f"Reference export contains duplicate CDE: {cde_key}")
        cdes.append(cde)
        pvs[cde_key] = values
    return ReferenceModel(
        version=DataModelVersionReference(key, version),
        label=label,
        catalog=CdeCatalog.from_cdes(cdes),
        pvs=CdePvCatalog.from_mapping(pvs),
    )


def _cde_from_payload(raw: object) -> tuple[CDEInfo, frozenset[str]]:
    if not isinstance(raw, Mapping):
        raise ReferenceDataCorruptError("Reference export CDE must be an object")
    cde_key = _string(raw, "cde_key")
    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        raise ReferenceDataCorruptError(f"Reference CDE description is invalid: {cde_key}")
    raw_cde_id = raw.get("cde_id")
    if (
        raw_cde_id is not None
        and (isinstance(raw_cde_id, bool) or not isinstance(raw_cde_id, int) or raw_cde_id < 0)
    ):
        raise ReferenceDataCorruptError(f"Reference CDE id is invalid: {cde_key}")
    try:
        cde_type = CdeType(_string(raw, "cde_type"))
    except ValueError as exc:
        raise ReferenceDataCorruptError(f"Reference CDE type is invalid: {cde_key}") from exc
    values = raw.get("values")
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ReferenceDataCorruptError(f"Reference CDE values are invalid: {cde_key}")
    typed_values = cast(list[str], values)
    if len(typed_values) != len(set(typed_values)):
        raise ReferenceDataCorruptError(f"Reference CDE values contain duplicates: {cde_key}")
    cde = CDEInfo(cast(int | None, raw_cde_id), cde_key, cast(str | None, description), cde_type)
    return cde, frozenset(typed_values)


def _string(raw: Mapping[object, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ReferenceDataCorruptError(f"Reference export field is invalid: {field}")
    return value


def _model_order(model: ReferenceModel) -> tuple[str, str]:
    return model.version.data_model_key, model.version.external_version_number


def _summaries(models: Sequence[ReferenceModel]) -> tuple[DataModelSummary, ...]:
    labels: dict[str, str] = {}
    versions: dict[str, list[DataModelVersionInfo]] = {}
    for model in models:
        labels[model.version.data_model_key] = model.label
        versions.setdefault(model.version.data_model_key, []).append(
            DataModelVersionInfo(model.version.external_version_number)
        )
    return tuple(
        DataModelSummary(
            data_model_key=key,
            label=labels[key],
            versions=sorted(
                model_versions,
                key=lambda version: version.external_version_number,
            ),
        )
        for key, model_versions in sorted(versions.items())
    )


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "FILE_SCHEMA_VERSION",
    "FileReferenceDataRepository",
    "load_reference_models",
    "save_reference_models",
]
