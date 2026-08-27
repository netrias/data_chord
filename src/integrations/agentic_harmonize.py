"""Harmonize terms with the agentic Bedrock provider."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import local

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

from src.domain.harmonization import MatchFidelity
from src.domain.harmonization_cache import HarmonizationCache
from src.integrations.harmonize import (
    HarmonizationWorkflowService,
    TermHarmonizationProvider,
    TermHarmonizationRequest,
    TermHarmonizationResponse,
)


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


class _WorkerState(local):
    client: ConverseClient | None = None


class AgenticTermHarmonizer:
    """Run one bounded Bedrock request for each unique source term."""

    def __init__(self, config: AgenticHarmonizeConfig) -> None:
        self._config = config
        self._worker_state = _WorkerState()

    def harmonize(
        self,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResponse, ...]:
        if not requests:
            return ()
        indexes = _build_indexes(requests)
        responses: list[TermHarmonizationResponse | None] = [None] * len(requests)
        worker_count = min(self._config.max_workers, len(requests))
        indexed_requests = iter(enumerate(requests))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures: dict[Future[TermHarmonizationResponse], int] = {}
            for _ in range(worker_count):
                index, request = next(indexed_requests)
                future = executor.submit(
                    self._harmonize_term,
                    request,
                    indexes[request.permissible_values],
                )
                futures[future] = index
            try:
                while futures:
                    completed, _pending = wait(futures, return_when=FIRST_COMPLETED)
                    for future in completed:
                        responses[futures.pop(future)] = future.result()
                        next_item = next(indexed_requests, None)
                        if next_item is not None:
                            index, request = next_item
                            submitted = executor.submit(
                                self._harmonize_term,
                                request,
                                indexes[request.permissible_values],
                            )
                            futures[submitted] = index
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        if any(response is None for response in responses):
            raise RuntimeError("Agentic harmonization returned an incomplete result")
        return tuple(response for response in responses if response is not None)

    def _harmonize_term(
        self,
        request: TermHarmonizationRequest,
        indexes: tuple[PvsIndex, IndexBundle],
    ) -> TermHarmonizationResponse:
        result = harmonize_term(
            self._provider_client(),
            indexes[0],
            request.input_term,
            indexes=indexes[1],
            explorer_model=self._config.explorer_model,
            shortlister_model=self._config.shortlister_model,
            selector_model=self._config.selector_model,
            exploration_turns=self._config.exploration_turns,
            context=request.context,
        )
        predicted = result.prediction.predicted_match
        fidelity = (
            MatchFidelity.NONE
            if predicted == NO_MATCH
            else MatchFidelity(result.prediction.match_fidelity)
        )
        return TermHarmonizationResponse(
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


class AgenticHarmonizeService(HarmonizationWorkflowService):
    """Configure the provider-neutral file workflow for Bedrock."""

    def __init__(
        self,
        config: AgenticHarmonizeConfig,
        *,
        cache: HarmonizationCache | None = None,
        term_harmonization_provider: TermHarmonizationProvider | None = None,
    ) -> None:
        super().__init__(
            term_harmonization_provider or AgenticTermHarmonizer(config),
            cache=cache,
        )


def _build_indexes(
    requests: tuple[TermHarmonizationRequest, ...],
) -> dict[tuple[str, ...], tuple[PvsIndex, IndexBundle]]:
    indexes: dict[tuple[str, ...], tuple[PvsIndex, IndexBundle]] = {}
    for request in requests:
        if request.permissible_values in indexes:
            continue
        pvs_index = build_pvs_index(list(request.permissible_values))
        indexes[request.permissible_values] = (pvs_index, build_all_indexes(pvs_index))
    return indexes


__all__ = [
    "AgenticHarmonizeConfig",
    "AgenticHarmonizeService",
    "AgenticTermHarmonizer",
]
