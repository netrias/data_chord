"""Run agentic term harmonization inside the DataChord task."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from threading import local
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
from src.integrations.harmonize import HarmonizeResult
from src.local_inference import (
    LocalInferenceProvider,
    LocalInferenceRequest,
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
    permissible_values: frozenset[str] | None
    pvs_index: PvsIndex | None
    search_indexes: IndexBundle | None
    is_exact_match: bool

    @property
    def context(self) -> str:
        return f"Source column: {self.column_name}\nTarget CDE: {self.cde_key}"


@dataclass(frozen=True)
class _TermOutcome:
    work: _TermWork
    matched_value: str | None
    match_fidelity: MatchFidelity


class _WorkerState(local):
    client: ConverseClient | None = None


class AgenticHarmonizeService:
    def __init__(
        self,
        config: AgenticHarmonizeConfig,
        *,
        cache: HarmonizationCache | None = None,
        local_inference: LocalInferenceProvider | None = None,
    ) -> None:
        self._config = config
        self._cache = cache or EmptyHarmonizationCache()
        self._local_inference = local_inference
        self._worker_state = _WorkerState()

    def run(
        self,
        *,
        file_path: Path,
        data_model_version: DataModelVersionReference,
        prepared_manifest: ColumnMappingManifest,
        column_pv_sets: ColumnPvSets,
        output_path: Path | None = None,
        sheet_name: str | None = None,
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        requested_output = output_path or file_path.with_name(f"{file_path.stem}.harmonized{file_path.suffix}")
        manifest_path = requested_output.with_name(f"{requested_output.stem}.manifest.parquet")
        try:
            dataset = read_tabular(file_path, sheet_name)
            work = _build_work(dataset.columns, dataset.rows, prepared_manifest, column_pv_sets)
            outcomes = self._run_terms(work, data_model_version)
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
    ) -> list[_TermOutcome]:
        outcomes = [
            _passthrough(item)
            for item in work
            if item.pvs_index is None or not item.input_term.strip()
        ]
        outcomes.extend(_exact_match(item) for item in work if item.is_exact_match)
        provider_work = [
            item
            for item in work
            if item.pvs_index is not None
            and item.input_term.strip()
            and not item.is_exact_match
        ]
        local_work: list[_TermWork] = []
        bedrock_work: list[_TermWork] = []
        for item in provider_work:
            target = local_work if self._uses_local_inference(item) else bedrock_work
            target.append(item)
        outcomes.extend(self._run_local_terms(local_work))
        cache_work = [(item, _cache_key(data_model_version, item)) for item in bedrock_work]
        cached = self._load_cache([key for _item, key in cache_work])
        uncached_bedrock_work: list[_TermWork] = []
        for item, key in cache_work:
            entry = cached.get(key)
            if entry is None:
                uncached_bedrock_work.append(item)
            else:
                outcomes.append(_cached_outcome(item, entry))
        logger.info(
            "Prepared harmonization work",
            extra={
                "exact_matches": sum(item.is_exact_match for item in work),
                "local_terms": len(local_work),
                "cache_hits": len(cache_work) - len(uncached_bedrock_work),
                "bedrock_terms": len(uncached_bedrock_work),
            },
        )
        provider_outcomes = self._run_provider_terms(uncached_bedrock_work)
        outcomes.extend(provider_outcomes)
        self._save_cache(data_model_version, provider_outcomes)
        return sorted(outcomes, key=_outcome_order)

    def _uses_local_inference(self, work: _TermWork) -> bool:
        return self._local_inference is not None and work.cde_key in self._local_inference.supported_cdes

    def _run_local_terms(self, local_work: list[_TermWork]) -> list[_TermOutcome]:
        if not local_work:
            return []
        if self._local_inference is None:
            raise RuntimeError("Local harmonization work requires local inference")
        requests = tuple(
            LocalInferenceRequest(
                cde=item.cde_key,
                source_value=item.input_term,
                permissible_values=item.permissible_values or frozenset(),
            )
            for item in local_work
        )
        results = self._local_inference.harmonize(requests)
        if len(results) != len(local_work):
            raise RuntimeError("Local inference returned an incomplete result")
        return [
            _TermOutcome(
                work=item,
                matched_value=result.matched_value,
                match_fidelity=result.match_fidelity,
            )
            for item, result in zip(local_work, results, strict=True)
        ]

    def _run_provider_terms(self, provider_work: list[_TermWork]) -> list[_TermOutcome]:
        if not provider_work:
            return []
        outcomes: list[_TermOutcome] = []
        worker_count = min(self._config.max_workers, len(provider_work))
        work_iterator = iter(provider_work)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures: set[Future[_TermOutcome]] = {
                executor.submit(self._harmonize_term, next(work_iterator))
                for _ in range(worker_count)
            }
            try:
                while futures:
                    completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                    for future in completed:
                        outcomes.append(future.result())
                        if next_work := next(work_iterator, None):
                            futures.add(executor.submit(self._harmonize_term, next_work))
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

    def _harmonize_term(self, work: _TermWork) -> _TermOutcome:
        if work.pvs_index is None:
            raise RuntimeError("Agentic work requires a permissible value index")
        result = harmonize_term(
            self._provider_client(),
            work.pvs_index,
            work.input_term,
            indexes=work.search_indexes,
            explorer_model=self._config.explorer_model,
            shortlister_model=self._config.shortlister_model,
            selector_model=self._config.selector_model,
            exploration_turns=self._config.exploration_turns,
            context=work.context,
        )
        predicted = result.prediction.predicted_match
        fidelity = (
            MatchFidelity.NONE
            if predicted == NO_MATCH
            else MatchFidelity(result.prediction.match_fidelity)
        )
        return _TermOutcome(
            work=work,
            matched_value=None if predicted == NO_MATCH else predicted,
            match_fidelity=fidelity,
        )

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


def _build_work(
    columns: list[TabularColumn],
    rows: list[list[str]],
    manifest: ColumnMappingManifest,
    column_pv_sets: ColumnPvSets,
) -> list[_TermWork]:
    pvs_indexes: dict[frozenset[str], tuple[PvsIndex, IndexBundle]] = {}
    work: list[_TermWork] = []
    for column in columns:
        column_id = column.index
        column_key = column.key
        record = manifest.records.get(column_key_from_string(column_key))
        if record is None:
            continue
        pvs = column_pv_sets.get(column_key)
        indexes = _indexes_for(pvs, pvs_indexes)
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
                permissible_values=pvs,
                pvs_index=indexes[0] if indexes else None,
                search_indexes=indexes[1] if indexes else None,
                is_exact_match=pvs is not None and term in pvs,
            ))
    return work


def _indexes_for(
    pvs: frozenset[str] | None,
    cache: dict[frozenset[str], tuple[PvsIndex, IndexBundle]],
) -> tuple[PvsIndex, IndexBundle] | None:
    if not pvs:
        return None
    if pvs not in cache:
        pvs_index = build_pvs_index(sorted(pvs))
        cache[pvs] = (pvs_index, build_all_indexes(pvs_index))
    return cache[pvs]


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
