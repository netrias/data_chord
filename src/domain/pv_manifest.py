"""Version-bound permissible-value snapshot stored for later workflow stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference

_CURRENT_SCHEMA_VERSION: Final = 2


class PvManifestSchemaError(Exception):
    """Raised when a current PV snapshot is malformed or from a newer schema."""


@dataclass(frozen=True)
class PVManifest:
    """PVs bound to the exact model and workflow-state revision that produced them."""

    data_model_version: DataModelVersionReference
    workflow_state_version: str
    pvs: CdePvCatalog

    @classmethod
    def from_store(cls, payload: object) -> PVManifest:
        if not isinstance(payload, Mapping):
            raise PvManifestSchemaError("PV snapshot must be an object")
        schema_version = payload.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != _CURRENT_SCHEMA_VERSION
        ):
            raise PvManifestSchemaError(f"PV snapshot schema {schema_version} is not supported")

        data_model_key = payload.get("data_model_key")
        external_version_number = payload.get("external_version_number")
        if not isinstance(data_model_key, str) or not isinstance(external_version_number, str):
            raise PvManifestSchemaError("PV snapshot is missing its model version")
        try:
            data_model_version = DataModelVersionReference(data_model_key, external_version_number)
        except ValueError as exc:
            raise PvManifestSchemaError("PV snapshot has an invalid model version") from exc

        workflow_state_version = payload.get("workflow_state_version")
        if not isinstance(workflow_state_version, str):
            raise PvManifestSchemaError("PV snapshot is missing its workflow-state version")

        return cls(
            data_model_version=data_model_version,
            workflow_state_version=workflow_state_version,
            pvs=CdePvCatalog.from_mapping(_pv_mapping(payload.get("pvs"))),
        )

    def to_store(self) -> dict[str, object]:
        return {
            "schema_version": _CURRENT_SCHEMA_VERSION,
            "data_model_key": self.data_model_version.data_model_key,
            "external_version_number": self.data_model_version.external_version_number,
            "workflow_state_version": self.workflow_state_version,
            "pvs": {cde_key: sorted(values) for cde_key, values in self.pvs.values.items()},
        }


def _pv_mapping(value: object) -> dict[str, frozenset[str]]:
    if not isinstance(value, Mapping):
        raise PvManifestSchemaError("PV snapshot pvs must be an object")
    parsed: dict[str, frozenset[str]] = {}
    for cde_key, raw_values in value.items():
        if not isinstance(cde_key, str) or not isinstance(raw_values, list):
            raise PvManifestSchemaError("PV snapshot contains an invalid PV set")
        if any(not isinstance(item, str) for item in raw_values):
            raise PvManifestSchemaError(f"PV snapshot contains an invalid value for {cde_key}")
        parsed[cde_key] = frozenset(raw_values)
    return parsed


__all__ = ["PVManifest", "PvManifestSchemaError"]
