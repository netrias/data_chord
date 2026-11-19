"""Complete, provider-independent reference data for one standard version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.domain.cde import CdeType, DataModelSummary
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference


class ReferenceDataError(Exception):
    """Base error at the trusted reference-data boundary."""


class ReferenceDataUnavailableError(ReferenceDataError):
    """The reference store could not serve a request."""


class ReferenceModelNotFoundError(ReferenceDataError):
    """The requested model version is not published."""


class ReferenceDataCorruptError(ReferenceDataError):
    """Stored reference data failed its integrity checks."""


@dataclass(frozen=True)
class ReferenceModel:
    """All CDE metadata and explicit value sets for one model version."""

    version: DataModelVersionReference
    label: str
    catalog: CdeCatalog
    pvs: CdePvCatalog

    def __post_init__(self) -> None:
        if not self.version.data_model_key.strip():
            raise ValueError("Reference model key is required")
        if not self.label.strip():
            raise ValueError("Reference model label is required")
        catalog_keys = self.catalog.keys()
        if any(not cde_key.strip() for cde_key in catalog_keys):
            raise ValueError("Reference model CDE keys are required")
        if len(catalog_keys) != len(set(catalog_keys)):
            raise ValueError("Reference model CDE keys must be unique")
        if set(catalog_keys) != set(self.pvs.values):
            raise ValueError("Reference model CDE and value-set keys must exactly match")
        for cde in self.catalog:
            has_values = bool(self.pvs.get(cde.cde_key))
            if has_values != (cde.cde_type == CdeType.PV):
                raise ValueError(f"Reference model CDE type does not match its values: {cde.cde_key}")
        object.__setattr__(self, "catalog", CdeCatalog.from_cdes(sorted(self.catalog, key=lambda cde: cde.cde_key)))


class ReferenceDataRepository(Protocol):
    """The only reference-data interface used by workflow stages."""

    def list_models(self) -> tuple[DataModelSummary, ...]: ...

    def load_model(self, version: DataModelVersionReference) -> ReferenceModel: ...


__all__ = [
    "ReferenceDataCorruptError",
    "ReferenceDataError",
    "ReferenceDataRepository",
    "ReferenceDataUnavailableError",
    "ReferenceModel",
    "ReferenceModelNotFoundError",
]
