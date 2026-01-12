"""
CDE data types for data model integration.

Supports both legacy hardcoded CDEs and dynamic CDEs fetched from API.
The legacy types (CDEField, CDE_REGISTRY, etc.) are deprecated and will be removed
once all usages are migrated to dynamic CDEInfo-based types.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

from src.domain.config import get_data_model_key


def get_default_target_schema() -> str:
    return get_data_model_key()


@dataclass(frozen=True)
class CDEInfo:
    """Dynamic CDE metadata fetched from the Data Model Store API."""

    cde_id: int
    cde_key: str
    description: str | None
    version_label: str


class ModelSuggestion(BaseModel):
    target: str
    similarity: float


class CDEField(str, Enum):
    PRIMARY_DIAGNOSIS = "primary_diagnosis"
    MORPHOLOGY = "morphology"
    SAMPLE_ANATOMIC_SITE = "sample_anatomic_site"
    THERAPEUTIC_AGENTS = "therapeutic_agents"
    TISSUE_OR_ORGAN_OF_ORIGIN = "tissue_or_organ_of_origin"


@dataclass(frozen=True)
class CDEDefinition:
    field: CDEField
    label: str
    cde_id: int
    route: str


# TODO: Replace with values fetched from API endpoints
CDE_REGISTRY: Mapping[CDEField, CDEDefinition] = {
    CDEField.PRIMARY_DIAGNOSIS: CDEDefinition(
        field=CDEField.PRIMARY_DIAGNOSIS,
        label="primary_diagnosis",
        cde_id=2,
        route="sagemaker:primary",
    ),
    CDEField.MORPHOLOGY: CDEDefinition(
        field=CDEField.MORPHOLOGY,
        label="morphology",
        cde_id=3,
        route="sagemaker:morphology",
    ),
    CDEField.SAMPLE_ANATOMIC_SITE: CDEDefinition(
        field=CDEField.SAMPLE_ANATOMIC_SITE,
        label="sample_anatomic_site",
        cde_id=5,
        route="sagemaker:sample_anatomic_site",
    ),
    CDEField.THERAPEUTIC_AGENTS: CDEDefinition(
        field=CDEField.THERAPEUTIC_AGENTS,
        label="therapeutic_agents",
        cde_id=1,
        route="sagemaker:therapeutic_agents",
    ),
    CDEField.TISSUE_OR_ORGAN_OF_ORIGIN: CDEDefinition(
        field=CDEField.TISSUE_OR_ORGAN_OF_ORIGIN,
        label="tissue_or_organ_of_origin",
        cde_id=4,
        route="sagemaker:tissue_or_organ_of_origin",
    ),
}

# TODO: Replace with values fetched from API endpoints
TARGET_ALIAS_MAP: Mapping[str, CDEField] = {
    "primary diagnosis": CDEField.PRIMARY_DIAGNOSIS,
    "primary_diagnosis": CDEField.PRIMARY_DIAGNOSIS,
    "morphology": CDEField.MORPHOLOGY,
    "sample anatomic site": CDEField.SAMPLE_ANATOMIC_SITE,
    "sample_anatomic_site": CDEField.SAMPLE_ANATOMIC_SITE,
    "therapeutic agents": CDEField.THERAPEUTIC_AGENTS,
    "therapeutic_agents": CDEField.THERAPEUTIC_AGENTS,
    "tissue or organ of origin": CDEField.TISSUE_OR_ORGAN_OF_ORIGIN,
    "tissue_or_organ_of_origin": CDEField.TISSUE_OR_ORGAN_OF_ORIGIN,
}


def get_cde(field: CDEField) -> CDEDefinition:
    return CDE_REGISTRY[field]


def get_all_cdes() -> list[CDEDefinition]:
    return list(CDE_REGISTRY.values())


def get_cde_labels() -> list[str]:
    return [defn.label for defn in CDE_REGISTRY.values()]


def normalize_target_name(selection: str | None) -> CDEField | None:
    if not selection:
        return None
    cleaned = selection.strip().lower().replace("-", " ")
    slug = "_".join(part for part in cleaned.split() if part)
    if not slug:
        return None
    return TARGET_ALIAS_MAP.get(slug)


@dataclass(frozen=True)
class ColumnMapping:
    column_name: str
    target: CDEField | None  # None means explicitly "No AI Recommendation"


@dataclass(frozen=True)
class ColumnMappingSet:
    mappings: tuple[ColumnMapping, ...]

    @classmethod
    def from_dict(cls, overrides: Mapping[str, str]) -> ColumnMappingSet:
        mappings: list[ColumnMapping] = []
        for column, selection in overrides.items():
            target = normalize_target_name(selection)
            mappings.append(ColumnMapping(column_name=column, target=target))
        return cls(mappings=tuple(mappings))

    def to_dict(self) -> dict[str, str | None]:
        return {
            m.column_name: m.target.value if m.target else None
            for m in self.mappings
        }

    def get_applied(self) -> list[ColumnMapping]:
        return [m for m in self.mappings if m.target is not None]

    def get_skipped(self) -> list[str]:
        return [m.column_name for m in self.mappings if m.target is None]
