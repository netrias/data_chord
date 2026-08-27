"""Group local harmonization work while hiding model selection and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from src.domain.harmonization import MatchFidelity
from src.local_inference.catalog import LocalModelCatalog, LocalModelDefinition


class LocalInferenceError(Exception):
    """Local inference could not return a complete, valid result."""


class UnsupportedCdeError(KeyError):
    """A caller sent a CDE that is not assigned to a local model."""


@dataclass(frozen=True)
class LocalInferenceRequest:
    cde: str
    source_value: str
    permissible_values: frozenset[str]

    def __post_init__(self) -> None:
        if not self.cde.strip():
            raise ValueError("Local inference CDE is required")
        if not self.source_value.strip():
            raise ValueError("Local inference source value is required")
        if not self.permissible_values:
            raise ValueError("Local inference requires permissible values")


@dataclass(frozen=True)
class LocalInferenceResult:
    matched_value: str | None
    match_fidelity: MatchFidelity

    def __post_init__(self) -> None:
        if self.matched_value is not None and not self.matched_value.strip():
            raise ValueError("Local inference matched value must be non-empty")
        if (self.matched_value is None) != (self.match_fidelity is MatchFidelity.NONE):
            raise ValueError("Local inference fidelity must agree with its matched value")


class LocalModelRunner(Protocol):
    def harmonize(
        self,
        model_path: Path,
        requests: tuple[LocalInferenceRequest, ...],
    ) -> tuple[LocalInferenceResult, ...]: ...


class LocalInferenceProvider(Protocol):
    @property
    def supported_cdes(self) -> frozenset[str]: ...

    def harmonize(
        self,
        requests: tuple[LocalInferenceRequest, ...],
    ) -> tuple[LocalInferenceResult, ...]: ...


class LocalInference:
    """The application's complete boundary to local model inference."""

    def __init__(self, catalog: LocalModelCatalog, runner: LocalModelRunner) -> None:
        self._catalog = catalog
        self._runner = runner

    @property
    def supported_cdes(self) -> frozenset[str]:
        return self._catalog.supported_cdes

    def harmonize(
        self,
        requests: tuple[LocalInferenceRequest, ...],
    ) -> tuple[LocalInferenceResult, ...]:
        assignments: dict[LocalModelDefinition, list[tuple[int, LocalInferenceRequest]]] = {
            model: [] for model in self._catalog.models
        }
        for index, request in enumerate(requests):
            try:
                model = self._catalog.model_for(request.cde)
            except KeyError as exc:
                raise UnsupportedCdeError(request.cde) from exc
            assignments[model].append((index, request))

        ordered_results: list[LocalInferenceResult | None] = [None] * len(requests)
        for model in self._catalog.models:
            model_assignments = assignments[model]
            if not model_assignments:
                continue
            model_requests = tuple(request for _index, request in model_assignments)
            model_results = self._runner.harmonize(
                self._catalog.model_path(model),
                model_requests,
            )
            if len(model_results) != len(model_requests):
                raise LocalInferenceError(
                    f"Local model {model.relative_path} returned {len(model_results)} results "
                    f"for {len(model_requests)} requests"
                )
            for (request_index, request), result in zip(model_assignments, model_results, strict=True):
                if result.matched_value is not None and result.matched_value not in request.permissible_values:
                    raise LocalInferenceError(
                        f"Local model {model.relative_path} returned a value outside CDE {request.cde}"
                    )
                ordered_results[request_index] = result

        if any(result is None for result in ordered_results):
            raise LocalInferenceError("Local inference did not return every requested result")
        return tuple(cast(LocalInferenceResult, result) for result in ordered_results)


__all__ = [
    "LocalInference",
    "LocalInferenceError",
    "LocalInferenceRequest",
    "LocalInferenceResult",
    "LocalInferenceProvider",
    "LocalModelRunner",
    "UnsupportedCdeError",
]
