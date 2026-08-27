"""Provider-neutral harmonization result contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import HarmonizeStatus, MatchFidelity
from src.domain.manifest import ColumnMappingManifest
from src.persistence.pv_manifest_store import ColumnPvSets


@dataclass(frozen=True)
class HarmonizeResult:
    job_id: str
    status: HarmonizeStatus
    detail: str
    job_id_available: bool = False
    manifest_path: Path | None = None
    output_path: Path | None = None


class HarmonizeService(Protocol):
    def run(
        self,
        *,
        file_path: Path,
        data_model_version: DataModelVersionReference,
        prepared_manifest: ColumnMappingManifest,
        column_pv_sets: ColumnPvSets,
        output_path: Path | None = None,
        sheet_name: str | None = None,
        use_cache: bool = True,
    ) -> HarmonizeResult: ...


@dataclass(frozen=True)
class TermHarmonizationRequest:
    """Provider-neutral input for harmonizing one distinct source term."""

    input_term: str
    permissible_values: tuple[str, ...]
    context: str


@dataclass(frozen=True)
class TermHarmonizationResponse:
    """Provider-neutral result for one term."""

    matched_value: str | None
    match_fidelity: MatchFidelity


class TermHarmonizationProvider(Protocol):
    def harmonize(self, request: TermHarmonizationRequest) -> TermHarmonizationResponse: ...


class InvalidTermHarmonizationResponseError(RuntimeError):
    """The provider returned a result outside the requested permissible values."""


__all__ = [
    "HarmonizeResult",
    "HarmonizeService",
    "InvalidTermHarmonizationResponseError",
    "TermHarmonizationProvider",
    "TermHarmonizationRequest",
    "TermHarmonizationResponse",
]
