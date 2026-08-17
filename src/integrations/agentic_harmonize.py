"""Run agentic term harmonization inside the DataChord task."""

from __future__ import annotations

import logging
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
from src.domain.harmonization import HarmonizeStatus, MatchFidelity
from src.domain.manifest import ColumnMappingManifest, ManifestRow
from src.integrations.harmonize import HarmonizeResult
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
    max_workers: int = 50

    def __post_init__(self) -> None:
        if not self.region.strip():
            raise ValueError("Agentic harmonization requires an AWS region")
        if self.exploration_turns < 1:
            raise ValueError("Agentic exploration turns must be positive")
        if self.max_workers < 1:
            raise ValueError("Agentic worker count must be positive")


@dataclass(frozen=True)
class _TermWork:
    column_id: int
    column_name: str
    cde_key: str
    input_term: str
    row_indices: tuple[int, ...]
    pvs_index: PvsIndex | None
    search_indexes: IndexBundle | None

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
    def __init__(self, config: AgenticHarmonizeConfig) -> None:
        self._config = config
        self._worker_state = _WorkerState()

    def run(
        self,
        *,
        file_path: Path,
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
            outcomes = self._run_terms(work)
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

    def _run_terms(self, work: list[_TermWork]) -> list[_TermOutcome]:
        passthrough = [_passthrough(item) for item in work if item.pvs_index is None or not item.input_term.strip()]
        provider_work = [item for item in work if item.pvs_index is not None and item.input_term.strip()]
        if not provider_work:
            return sorted(passthrough, key=_outcome_order)

        outcomes = list(passthrough)
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
        return sorted(outcomes, key=_outcome_order)

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
                pvs_index=indexes[0] if indexes else None,
                search_indexes=indexes[1] if indexes else None,
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
        manual_overrides=[],
    )


__all__ = ["AgenticHarmonizeConfig", "AgenticHarmonizeService"]
