"""
Define the canonical CDE field registry for CCDI harmonization.

Single source of truth for CDE column identifiers, labels, IDs, and routes.

NOTE: This is a temporary mock. CDE definitions will eventually come from API endpoints.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

DEFAULT_TARGET_SCHEMA: str = "ccdi"


class CDEField(str, Enum):
    """why: canonical identifiers for the 5 CCDI CDE columns."""

    PRIMARY_DIAGNOSIS = "primary_diagnosis"
    MORPHOLOGY = "morphology"
    SAMPLE_ANATOMIC_SITE = "sample_anatomic_site"
    THERAPEUTIC_AGENTS = "therapeutic_agents"
    TISSUE_OR_ORGAN_OF_ORIGIN = "tissue_or_organ_of_origin"


@dataclass(frozen=True)
class CDEDefinition:
    """why: complete metadata for a single CDE field."""

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
    """why: retrieve metadata for a specific CDE field."""
    return CDE_REGISTRY[field]


def get_all_cdes() -> list[CDEDefinition]:
    """why: return all CDE definitions in display order."""
    return list(CDE_REGISTRY.values())


def get_cde_labels() -> list[str]:
    """why: return human-readable labels for UI dropdowns."""
    return [defn.label for defn in CDE_REGISTRY.values()]


def normalize_target_name(selection: str | None) -> CDEField | None:
    """why: convert user input to canonical CDEField, handling variations."""
    if not selection:
        return None
    cleaned = selection.strip().lower().replace("-", " ")
    slug = "_".join(part for part in cleaned.split() if part)
    if not slug:
        return None
    return TARGET_ALIAS_MAP.get(slug)
