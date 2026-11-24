"""Group local term requests while hiding model selection and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from src.integrations.harmonize import TermHarmonizationRequest, TermHarmonizationResponse
from src.local_inference.catalog import LocalModelCatalog, LocalModelConfig


class LocalInferenceError(Exception):
    """Local inference could not return a complete, valid result."""


class UnsupportedCdeError(KeyError):
    """A local request names a CDE without a model assignment."""


class LocalModelRunner(Protocol):
    def harmonize(
        self,
        model_path: Path,
        model_config: LocalModelConfig,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResponse, ...]: ...


class LocalTermHarmonizer:
    """The provider-neutral harmonization boundary for local models."""

    def __init__(self, catalog: LocalModelCatalog, runner: LocalModelRunner) -> None:
        self._catalog = catalog
        self._runner = runner

    def harmonize(
        self,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResponse, ...]:
        assignments = self._assign_requests(requests)
        ordered_results: list[TermHarmonizationResponse | None] = [None] * len(requests)
        for model in self._catalog.models:
            batch = assignments[model]
            if batch:
                self._run_model_batch(model, batch, ordered_results)
        if any(result is None for result in ordered_results):
            raise LocalInferenceError("Local inference did not return every requested result")
        return tuple(cast(TermHarmonizationResponse, result) for result in ordered_results)

    def _assign_requests(
        self,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> dict[LocalModelConfig, list[_AssignedRequest]]:
        assignments: dict[LocalModelConfig, list[_AssignedRequest]] = {model: [] for model in self._catalog.models}
        for index, request in enumerate(requests):
            try:
                model = self._catalog.model_for(request.cde)
            except KeyError as exc:
                raise UnsupportedCdeError(request.cde) from exc
            assignments[model].append(_AssignedRequest(index, request))
        return assignments

    def _run_model_batch(
        self,
        model: LocalModelConfig,
        batch: list[_AssignedRequest],
        ordered_results: list[TermHarmonizationResponse | None],
    ) -> None:
        requests = tuple(item.request for item in batch)
        results = self._runner.harmonize(self._catalog.model_path(model), model, requests)
        if len(results) != len(requests):
            raise LocalInferenceError(
                f"Local model {model.relative_path} returned {len(results)} results for {len(requests)} requests"
            )
        for item, result in zip(batch, results, strict=True):
            if result.matched_value is not None and result.matched_value not in item.request.permissible_values:
                raise LocalInferenceError(
                    f"Local model {model.relative_path} returned a value outside CDE {item.request.cde}"
                )
            ordered_results[item.index] = result


@dataclass(frozen=True)
class _AssignedRequest:
    index: int
    request: TermHarmonizationRequest


__all__ = [
    "LocalTermHarmonizer",
    "LocalInferenceError",
    "LocalModelRunner",
    "UnsupportedCdeError",
]
