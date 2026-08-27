"""Group local term requests while hiding model selection and lifecycle."""

from __future__ import annotations

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


class LocalInference:
    """The provider-neutral harmonization boundary for local models."""

    def __init__(self, catalog: LocalModelCatalog, runner: LocalModelRunner) -> None:
        self._catalog = catalog
        self._runner = runner

    def harmonize(
        self,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResponse, ...]:
        assignments: dict[LocalModelConfig, list[tuple[int, TermHarmonizationRequest]]] = {
            model: [] for model in self._catalog.models
        }
        for index, request in enumerate(requests):
            try:
                model = self._catalog.model_for(request.cde)
            except KeyError as exc:
                raise UnsupportedCdeError(request.cde) from exc
            assignments[model].append((index, request))

        ordered_results: list[TermHarmonizationResponse | None] = [None] * len(requests)
        for model in self._catalog.models:
            model_assignments = assignments[model]
            if not model_assignments:
                continue
            model_requests = tuple(request for _index, request in model_assignments)
            model_results = self._runner.harmonize(
                self._catalog.model_path(model),
                model,
                model_requests,
            )
            if len(model_results) != len(model_requests):
                raise LocalInferenceError(
                    f"Local model {model.relative_path} returned {len(model_results)} results "
                    f"for {len(model_requests)} requests"
                )
            for (request_index, request), result in zip(
                model_assignments,
                model_results,
                strict=True,
            ):
                if result.matched_value is not None and result.matched_value not in request.permissible_values:
                    raise LocalInferenceError(
                        f"Local model {model.relative_path} returned a value outside CDE {request.cde}"
                    )
                ordered_results[request_index] = result

        if any(result is None for result in ordered_results):
            raise LocalInferenceError("Local inference did not return every requested result")
        return tuple(cast(TermHarmonizationResponse, result) for result in ordered_results)


__all__ = [
    "LocalInference",
    "LocalInferenceError",
    "LocalModelRunner",
    "UnsupportedCdeError",
]
