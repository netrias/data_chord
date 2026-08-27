"""Run the file harmonization workflow with one configured term provider."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from netrias_client import TabularColumn, read_tabular, write_tabular

from src.domain.columns import column_key_from_string
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import HarmonizeStatus, MatchFidelity
from src.domain.harmonization_cache import (
    EmptyHarmonizationCache,
    HarmonizationCache,
    HarmonizationCacheEntry,
    HarmonizationCacheError,
    HarmonizationCacheKey,
)
from src.domain.manifest import ColumnMappingManifest, ManifestRow
from src.persistence.manifest_writer import write_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets

logger = logging.getLogger(__name__)


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
    """Provider-neutral input for one distinct source term."""

    cde: str
    input_term: str
    permissible_values: tuple[str, ...]
    context: str


@dataclass(frozen=True)
class TermHarmonizationResponse:
    """Provider-neutral result for one term."""

    matched_value: str | None
    match_fidelity: MatchFidelity


class TermHarmonizationProvider(Protocol):
    def harmonize(
        self,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResponse, ...]: ...


class InvalidTermHarmonizationResponseError(RuntimeError):
    """The provider returned a result outside the requested permissible values."""


@dataclass(frozen=True)
class _TermWork:
    column_id: int
    column_name: str
    cde_key: str
    input_term: str
    row_indices: tuple[int, ...]
    permissible_values: tuple[str, ...] | None
    is_exact_match: bool

    @property
    def context(self) -> str:
        return f"Source column: {self.column_name}\nTarget CDE: {self.cde_key}"


@dataclass(frozen=True)
class _TermOutcome:
    work: _TermWork
    matched_value: str | None
    match_fidelity: MatchFidelity


class FileHarmonizationService:
    """Own file processing while delegating non-exact terms to one provider."""

    def __init__(
        self,
        provider: TermHarmonizationProvider,
        *,
        cache: HarmonizationCache | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache or EmptyHarmonizationCache()

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
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        requested_output = output_path or file_path.with_name(
            f"{file_path.stem}.harmonized{file_path.suffix}"
        )
        manifest_path = requested_output.with_name(f"{requested_output.stem}.manifest.parquet")
        try:
            dataset = read_tabular(file_path, sheet_name)
            work = _build_work(dataset.columns, dataset.rows, prepared_manifest, column_pv_sets)
            outcomes = self._run_terms(work, data_model_version, use_cache=use_cache)
            output_rows = _apply_outcomes(dataset.rows, outcomes)
            manifest_rows = [_manifest_row(job_id, outcome) for outcome in outcomes]
            requested_output.parent.mkdir(parents=True, exist_ok=True)
            write_tabular(requested_output, replace(dataset, rows=output_rows), template_path=file_path)
            if not write_manifest_parquet(manifest_path, manifest_rows):
                raise RuntimeError("Could not write harmonization manifest")
        except Exception as exc:
            logger.exception("Harmonization failed", exc_info=exc, extra={"job_id": job_id})
            requested_output.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            return HarmonizeResult(
                job_id=job_id,
                status=HarmonizeStatus.FAILED,
                detail="Harmonization provider failed.",
            )
        return HarmonizeResult(
            job_id=job_id,
            status=HarmonizeStatus.SUCCEEDED,
            detail="Harmonization completed.",
            manifest_path=manifest_path,
            output_path=requested_output,
        )

    def _run_terms(
        self,
        work: list[_TermWork],
        data_model_version: DataModelVersionReference,
        *,
        use_cache: bool,
    ) -> list[_TermOutcome]:
        outcomes = [
            _passthrough(item)
            for item in work
            if not item.permissible_values or not item.input_term.strip()
        ]
        outcomes.extend(_exact_match(item) for item in work if item.is_exact_match)
        cache_work = [
            (item, _cache_key(data_model_version, item))
            for item in work
            if item.permissible_values
            and item.input_term.strip()
            and not item.is_exact_match
        ]
        cached = self._load_cache([key for _item, key in cache_work]) if use_cache else {}
        provider_work: list[_TermWork] = []
        invalid_cache_entries = 0
        for item, key in cache_work:
            entry = cached.get(key)
            if entry is None or not _cache_entry_matches_work(item, key, entry):
                provider_work.append(item)
                invalid_cache_entries += entry is not None
            else:
                outcomes.append(_cached_outcome(item, entry))
        logger.info(
            "Prepared harmonization work",
            extra={
                "exact_matches": sum(item.is_exact_match for item in work),
                "cache_hits": len(cache_work) - len(provider_work),
                "invalid_cache_entries": invalid_cache_entries,
                "provider_terms": len(provider_work),
            },
        )
        provider_outcomes = self._run_provider_terms(provider_work)
        outcomes.extend(provider_outcomes)
        if use_cache:
            self._save_cache(data_model_version, provider_outcomes)
        return sorted(outcomes, key=_outcome_order)

    def _run_provider_terms(self, work: list[_TermWork]) -> list[_TermOutcome]:
        if not work:
            return []
        requests = tuple(
            TermHarmonizationRequest(
                cde=item.cde_key,
                input_term=item.input_term,
                permissible_values=item.permissible_values or (),
                context=item.context,
            )
            for item in work
        )
        responses = self._provider.harmonize(requests)
        if len(responses) != len(work):
            raise InvalidTermHarmonizationResponseError(
                "Harmonization provider returned an incomplete result"
            )
        return [
            _outcome_from_response(item, request, response)
            for item, request, response in zip(work, requests, responses, strict=True)
        ]

    def _load_cache(
        self,
        keys: list[HarmonizationCacheKey],
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        try:
            return self._cache.load_many(keys)
        except HarmonizationCacheError:
            logger.warning("Harmonization cache read failed; using the provider", exc_info=True)
            return {}

    def _save_cache(
        self,
        data_model_version: DataModelVersionReference,
        outcomes: list[_TermOutcome],
    ) -> None:
        entries = [
            HarmonizationCacheEntry(
                key=_cache_key(data_model_version, outcome.work),
                matched_value=outcome.matched_value,
                match_fidelity=outcome.match_fidelity,
            )
            for outcome in outcomes
        ]
        if not entries:
            return
        try:
            self._cache.save_many(entries)
        except HarmonizationCacheError:
            logger.warning("Harmonization cache write failed; result remains usable", exc_info=True)


def _outcome_from_response(
    work: _TermWork,
    request: TermHarmonizationRequest,
    response: TermHarmonizationResponse,
) -> _TermOutcome:
    if response.matched_value is None and response.match_fidelity is not MatchFidelity.NONE:
        raise InvalidTermHarmonizationResponseError(
            "An unmatched provider result must use none fidelity"
        )
    if response.matched_value is not None:
        if response.matched_value not in request.permissible_values:
            raise InvalidTermHarmonizationResponseError(
                "The provider result is not a permissible value"
            )
        if response.match_fidelity is MatchFidelity.NONE:
            raise InvalidTermHarmonizationResponseError(
                "A matched provider result must report match fidelity"
            )
    return _TermOutcome(
        work=work,
        matched_value=response.matched_value,
        match_fidelity=response.match_fidelity,
    )


def _build_work(
    columns: list[TabularColumn],
    rows: list[list[str]],
    manifest: ColumnMappingManifest,
    column_pv_sets: ColumnPvSets,
) -> list[_TermWork]:
    work: list[_TermWork] = []
    for column in columns:
        record = manifest.records.get(column_key_from_string(column.key))
        if record is None:
            continue
        pvs = column_pv_sets.get(column.key)
        permissible_values = tuple(sorted(pvs)) if pvs else None
        grouped: dict[str, list[int]] = {}
        for row_index, row in enumerate(rows):
            grouped.setdefault(row[column.index], []).append(row_index)
        for term, row_indices in grouped.items():
            work.append(
                _TermWork(
                    column_id=column.index,
                    column_name=column.header,
                    cde_key=record.cde_key,
                    input_term=term,
                    row_indices=tuple(row_indices),
                    permissible_values=permissible_values,
                    is_exact_match=pvs is not None and term in pvs,
                )
            )
    return work


def _passthrough(work: _TermWork) -> _TermOutcome:
    return _TermOutcome(work=work, matched_value=None, match_fidelity=MatchFidelity.NONE)


def _exact_match(work: _TermWork) -> _TermOutcome:
    return _TermOutcome(
        work=work,
        matched_value=work.input_term,
        match_fidelity=MatchFidelity.STRONG,
    )


def _cache_key(
    data_model_version: DataModelVersionReference,
    work: _TermWork,
) -> HarmonizationCacheKey:
    return HarmonizationCacheKey(
        data_model_version=data_model_version,
        cde_key=work.cde_key,
        source_value=work.input_term,
    )


def _cached_outcome(work: _TermWork, entry: HarmonizationCacheEntry) -> _TermOutcome:
    return _TermOutcome(
        work=work,
        matched_value=entry.matched_value,
        match_fidelity=entry.match_fidelity,
    )


def _cache_entry_matches_work(
    work: _TermWork,
    expected_key: HarmonizationCacheKey,
    entry: HarmonizationCacheEntry,
) -> bool:
    return (
        entry.key == expected_key
        and (
            entry.matched_value is None
            or (
                work.permissible_values is not None
                and entry.matched_value in work.permissible_values
            )
        )
    )


def _outcome_order(outcome: _TermOutcome) -> tuple[int, int]:
    return outcome.work.column_id, outcome.work.row_indices[0]


def _apply_outcomes(rows: list[list[str]], outcomes: list[_TermOutcome]) -> list[list[str]]:
    output = [list(row) for row in rows]
    for outcome in outcomes:
        if outcome.matched_value is None:
            continue
        for row_index in outcome.work.row_indices:
            output[row_index][outcome.work.column_id] = outcome.matched_value
    return output


def _manifest_row(job_id: str, outcome: _TermOutcome) -> ManifestRow:
    matched = outcome.matched_value or ""
    return ManifestRow(
        job_id=job_id,
        column_id=outcome.work.column_id,
        column_name=outcome.work.column_name,
        to_harmonize=outcome.work.input_term,
        top_harmonization=matched,
        ontology_id=outcome.work.cde_key,
        top_harmonizations=[matched] if matched else [],
        match_fidelity=outcome.match_fidelity,
        error=None,
        row_indices=list(outcome.work.row_indices),
    )


__all__ = [
    "FileHarmonizationService",
    "HarmonizeResult",
    "HarmonizeService",
    "InvalidTermHarmonizationResponseError",
    "TermHarmonizationProvider",
    "TermHarmonizationRequest",
    "TermHarmonizationResponse",
]
