"""Run agentic term harmonization inside the DataChord task."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock, local
from uuid import uuid4

from agent_experiment import (
    GPT_5_6_LUNA,
    GPT_5_6_SOL,
    NO_MATCH,
    ConverseClient,
    IndexBundle,
    Model,
    Provider,
    PvsIndex,
    ReasoningEffort,
    build_all_indexes,
    build_pvs_index,
    harmonize_term,
    make_provider_client,
)
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
from src.integrations.harmonize import (
    HarmonizeResult,
    InvalidTermHarmonizationResponseError,
    TermHarmonizationProvider,
    TermHarmonizationRequest,
    TermHarmonizationResponse,
)
from src.persistence.manifest_writer import write_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgenticHarmonizeConfig:
    region: str
    explorer_model: Model = GPT_5_6_LUNA
    shortlister_model: Model = GPT_5_6_LUNA
    selector_model: Model = GPT_5_6_SOL
    reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH
    exploration_turns: int = 10
    max_workers: int = 100

    def __post_init__(self) -> None:
        if not self.region.strip():
            raise ValueError("Agentic harmonization requires an AWS region")
        if self.exploration_turns < 1:
            raise ValueError("Agentic exploration turns must be positive")
        if self.max_workers < 1:
            raise ValueError("Agentic worker count must be positive")
        if self.max_workers > 100:
            raise ValueError("Agentic worker count must not exceed 100")


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


class _ProviderWorkerState(local):
    client: ConverseClient | None = None


class _BedrockTermHarmonizationProvider:
    """Adapt the current agent-experiment call to the provider boundary."""

    def __init__(self, config: AgenticHarmonizeConfig) -> None:
        self._config = config
        self._worker_state = _ProviderWorkerState()
        self._index_cache: dict[tuple[str, ...], tuple[PvsIndex, IndexBundle]] = {}
        self._index_cache_lock = Lock()

    def harmonize(self, request: TermHarmonizationRequest) -> TermHarmonizationResponse:
        if not request.permissible_values:
            raise RuntimeError("Provider work requires permissible values")
        pvs_index, search_indexes = self._indexes_for(request.permissible_values)
        result = harmonize_term(
            self._provider_client(),
            pvs_index,
            request.input_term,
            indexes=search_indexes,
            explorer_model=self._config.explorer_model,
            shortlister_model=self._config.shortlister_model,
            selector_model=self._config.selector_model,
            exploration_turns=self._config.exploration_turns,
            context=request.context,
        )
        predicted = result.prediction.predicted_match
        return TermHarmonizationResponse(
            matched_value=None if predicted == NO_MATCH else predicted,
            match_fidelity=(
                MatchFidelity.NONE
                if predicted == NO_MATCH
                else MatchFidelity(result.prediction.match_fidelity)
            ),
        )

    def _indexes_for(
        self,
        permissible_values: tuple[str, ...],
    ) -> tuple[PvsIndex, IndexBundle]:
        with self._index_cache_lock:
            indexes = self._index_cache.get(permissible_values)
            if indexes is None:
                pvs_index = build_pvs_index(list(permissible_values))
                indexes = (pvs_index, build_all_indexes(pvs_index))
                self._index_cache[permissible_values] = indexes
            return indexes

    def _provider_client(self) -> ConverseClient:
        client = self._worker_state.client
        if client is None:
            client = make_provider_client(
                self._config.region,
                provider=Provider.BEDROCK,
                reasoning_effort=self._config.reasoning_effort,
            )
            self._worker_state.client = client
        return client


class AgenticHarmonizeService:
    def __init__(
        self,
        config: AgenticHarmonizeConfig,
        *,
        cache: HarmonizationCache | None = None,
        term_harmonization_provider: TermHarmonizationProvider | None = None,
    ) -> None:
        self._config = config
        self._cache = cache or EmptyHarmonizationCache()
        self._term_harmonization_provider = term_harmonization_provider

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
        requested_output = output_path or file_path.with_name(f"{file_path.stem}.harmonized{file_path.suffix}")
        manifest_path = requested_output.with_name(f"{requested_output.stem}.manifest.parquet")
        try:
            dataset = read_tabular(file_path, sheet_name)
            work = _build_work(dataset.columns, dataset.rows, prepared_manifest, column_pv_sets)
            provider = self._term_harmonization_provider or _BedrockTermHarmonizationProvider(
                self._config
            )
            outcomes = self._run_terms(
                work,
                data_model_version,
                provider=provider,
                use_cache=use_cache,
            )
            output_rows = _apply_outcomes(dataset.rows, outcomes)
            manifest_rows = [_manifest_row(job_id, outcome) for outcome in outcomes]
            requested_output.parent.mkdir(parents=True, exist_ok=True)
            write_tabular(requested_output, replace(dataset, rows=output_rows), template_path=file_path)
            if not write_manifest_parquet(manifest_path, manifest_rows):
                raise RuntimeError("Could not write harmonization manifest")
        except Exception as exc:
            logger.exception("Agentic harmonization failed", exc_info=exc, extra={"job_id": job_id})
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
        provider: TermHarmonizationProvider,
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
        provider_outcomes = self._run_provider_terms(provider_work, provider)
        outcomes.extend(provider_outcomes)
        if use_cache:
            self._save_cache(data_model_version, provider_outcomes)
        return sorted(outcomes, key=_outcome_order)

    def _run_provider_terms(
        self,
        provider_work: list[_TermWork],
        provider: TermHarmonizationProvider,
    ) -> list[_TermOutcome]:
        if not provider_work:
            return []
        outcomes: list[_TermOutcome] = []
        worker_count = min(self._config.max_workers, len(provider_work))
        work_iterator = iter(provider_work)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures: set[Future[_TermOutcome]] = {
                executor.submit(self._harmonize_term, next(work_iterator), provider)
                for _ in range(worker_count)
            }
            try:
                while futures:
                    completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                    for future in completed:
                        outcomes.append(future.result())
                        if next_work := next(work_iterator, None):
                            futures.add(executor.submit(self._harmonize_term, next_work, provider))
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        return outcomes

    def _load_cache(
        self,
        keys: list[HarmonizationCacheKey],
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        try:
            return self._cache.load_many(keys)
        except HarmonizationCacheError:
            logger.warning("Harmonization cache read failed; using Bedrock", exc_info=True)
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

    def _harmonize_term(
        self,
        work: _TermWork,
        provider: TermHarmonizationProvider,
    ) -> _TermOutcome:
        if not work.permissible_values:
            raise RuntimeError("Agentic work requires a permissible value index")
        result = provider.harmonize(
            TermHarmonizationRequest(
                input_term=work.input_term,
                permissible_values=work.permissible_values,
                context=work.context,
            )
        )
        if result.matched_value is None and result.match_fidelity is not MatchFidelity.NONE:
            raise InvalidTermHarmonizationResponseError(
                "An unmatched provider result must use none fidelity"
            )
        if result.matched_value is not None:
            if result.matched_value not in work.permissible_values:
                raise InvalidTermHarmonizationResponseError(
                    "The provider result is not a permissible value"
                )
            if result.match_fidelity is MatchFidelity.NONE:
                raise InvalidTermHarmonizationResponseError(
                    "A matched provider result must report match fidelity"
                )
        return _TermOutcome(
            work=work,
            matched_value=result.matched_value,
            match_fidelity=result.match_fidelity,
        )


def _build_work(
    columns: list[TabularColumn],
    rows: list[list[str]],
    manifest: ColumnMappingManifest,
    column_pv_sets: ColumnPvSets,
) -> list[_TermWork]:
    work: list[_TermWork] = []
    for column in columns:
        column_id = column.index
        column_key = column.key
        record = manifest.records.get(column_key_from_string(column_key))
        if record is None:
            continue
        pvs = column_pv_sets.get(column_key)
        permissible_values = tuple(sorted(pvs)) if pvs else None
        grouped: dict[str, list[int]] = {}
        for row_index, row in enumerate(rows):
            grouped.setdefault(row[column_id], []).append(row_index)
        for term, row_indices in grouped.items():
            work.append(_TermWork(
                column_id=column_id,
                column_name=column.header,
                cde_key=record.cde_key,
                input_term=term,
                row_indices=tuple(row_indices),
                permissible_values=permissible_values,
                is_exact_match=pvs is not None and term in pvs,
            ))
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


def _cached_outcome(
    work: _TermWork,
    entry: HarmonizationCacheEntry,
) -> _TermOutcome:
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


__all__ = ["AgenticHarmonizeConfig", "AgenticHarmonizeService"]
