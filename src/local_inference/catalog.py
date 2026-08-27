"""Load one JSON file into a trusted local-model catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast


class LocalModelConfigurationError(Exception):
    """The local-model file cannot safely configure inference."""


@dataclass(frozen=True)
class LocalModelConfig:
    """The checked settings for one model directory."""

    relative_path: Path
    cdes: frozenset[str]
    batch_size: int
    strong_confidence: float


@dataclass(frozen=True)
class LocalInferenceConfig:
    """The typed representation of the complete JSON file."""

    models: tuple[LocalModelConfig, ...]


@dataclass(frozen=True)
class LocalModelCatalog:
    """The model root and the derived CDE assignment index."""

    root: Path
    config: LocalInferenceConfig
    _models_by_cde: MappingProxyType[str, LocalModelConfig] = field(repr=False)

    @property
    def models(self) -> tuple[LocalModelConfig, ...]:
        return self.config.models

    @property
    def supported_cdes(self) -> frozenset[str]:
        return frozenset(self._models_by_cde)

    def model_for(self, cde: str) -> LocalModelConfig:
        return self._models_by_cde[cde]

    def model_path(self, model: LocalModelConfig) -> Path:
        return self.root / model.relative_path


def load_model_catalog(config_path: Path, models_root: Path) -> LocalModelCatalog:
    """Convert JSON to typed config, then derive and validate the runtime catalog."""
    root = models_root.resolve()
    config = _load_config(config_path)
    models_by_cde: dict[str, LocalModelConfig] = {}
    paths: set[Path] = set()
    for model in config.models:
        _validate_model_path(root, model, paths)
        _index_model_cdes(model, models_by_cde)
    return LocalModelCatalog(
        root=root,
        config=config,
        _models_by_cde=MappingProxyType(models_by_cde),
    )


def _load_config(config_path: Path) -> LocalInferenceConfig:
    payload = _load_json_object(config_path)
    if set(payload) != {"models"}:
        raise LocalModelConfigurationError("Local model file must contain only 'models'")
    raw_models = payload["models"]
    if not isinstance(raw_models, list) or not raw_models:
        raise LocalModelConfigurationError("Local model file must contain at least one model")
    return LocalInferenceConfig(
        models=tuple(_model_config(raw_model, index) for index, raw_model in enumerate(raw_models))
    )


def _load_json_object(config_path: Path) -> dict[str, object]:
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LocalModelConfigurationError(f"Cannot read local model file: {config_path}") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise LocalModelConfigurationError(f"Local model file is not valid JSON: {config_path}") from exc
    if not isinstance(payload, dict):
        raise LocalModelConfigurationError("Local model file must contain one JSON object")
    return cast(dict[str, object], payload)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LocalModelConfigurationError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _model_config(raw_model: object, index: int) -> LocalModelConfig:
    fields = {"path", "cdes", "batch_size", "strong_confidence"}
    if not isinstance(raw_model, dict) or set(raw_model) != fields:
        raise LocalModelConfigurationError(
            f"Local model at index {index} must contain only {', '.join(sorted(fields))}"
        )
    return LocalModelConfig(
        relative_path=_relative_path(raw_model["path"], index),
        cdes=_cde_set(raw_model["cdes"], index),
        batch_size=_batch_size(raw_model["batch_size"], index),
        strong_confidence=_strong_confidence(raw_model["strong_confidence"], index),
    )


def _relative_path(raw_path: object, index: int) -> Path:
    if not isinstance(raw_path, str) or not raw_path or raw_path.strip() != raw_path:
        raise LocalModelConfigurationError(f"Local model path at index {index} is invalid")
    path = Path(raw_path)
    if path.is_absolute() or path == Path("."):
        raise LocalModelConfigurationError(f"Local model path must be a relative subdirectory: {raw_path}")
    return path


def _cde_set(raw_cdes: object, index: int) -> frozenset[str]:
    if not isinstance(raw_cdes, list) or not raw_cdes:
        raise LocalModelConfigurationError(f"Local model CDEs at index {index} must be a non-empty list")
    cdes: list[str] = []
    for raw_cde in raw_cdes:
        if not isinstance(raw_cde, str) or not raw_cde or raw_cde.strip() != raw_cde:
            raise LocalModelConfigurationError(f"Local model CDE at index {index} is invalid")
        if raw_cde in cdes:
            raise LocalModelConfigurationError(f"Duplicate CDE at model index {index}: {raw_cde}")
        cdes.append(raw_cde)
    return frozenset(cdes)


def _batch_size(raw_batch_size: object, index: int) -> int:
    if not isinstance(raw_batch_size, int) or isinstance(raw_batch_size, bool) or raw_batch_size < 1:
        raise LocalModelConfigurationError(f"Local model batch_size at index {index} must be positive")
    return raw_batch_size


def _strong_confidence(raw_confidence: object, index: int) -> float:
    if not isinstance(raw_confidence, int | float) or isinstance(raw_confidence, bool):
        raise LocalModelConfigurationError(f"Local model strong_confidence at index {index} must be a number")
    confidence = float(raw_confidence)
    if confidence < 0 or confidence > 1:
        raise LocalModelConfigurationError(f"Local model strong_confidence at index {index} must be between 0 and 1")
    return confidence


def _validate_model_path(root: Path, model: LocalModelConfig, paths: set[Path]) -> None:
    model_path = (root / model.relative_path).resolve()
    if not model_path.is_relative_to(root):
        raise LocalModelConfigurationError(f"Local model path must stay inside {root}: {model.relative_path}")
    if model.relative_path in paths:
        raise LocalModelConfigurationError(f"Duplicate local model path: {model.relative_path}")
    if not model_path.is_dir():
        raise LocalModelConfigurationError(f"Local model directory does not exist: {model_path}")
    paths.add(model.relative_path)


def _index_model_cdes(
    model: LocalModelConfig,
    models_by_cde: dict[str, LocalModelConfig],
) -> None:
    for cde in model.cdes:
        previous = models_by_cde.get(cde)
        if previous is not None:
            raise LocalModelConfigurationError(
                f"CDE {cde} is assigned to both {previous.relative_path} and {model.relative_path}"
            )
        models_by_cde[cde] = model


__all__ = [
    "LocalInferenceConfig",
    "LocalModelCatalog",
    "LocalModelConfig",
    "LocalModelConfigurationError",
    "load_model_catalog",
]
