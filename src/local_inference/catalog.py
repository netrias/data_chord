"""Load one local-model file into a trusted CDE-to-model catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast


class LocalModelConfigurationError(Exception):
    """The local-model file cannot safely configure inference."""


@dataclass(frozen=True)
class LocalModelDefinition:
    """One model directory and the CDEs that it owns."""

    relative_path: Path
    cdes: frozenset[str]


@dataclass(frozen=True)
class LocalModelCatalog:
    """The validated, single source of truth for local model assignment."""

    root: Path
    models: tuple[LocalModelDefinition, ...]
    _models_by_cde: MappingProxyType[str, LocalModelDefinition] = field(repr=False)

    @property
    def supported_cdes(self) -> frozenset[str]:
        return frozenset(self._models_by_cde)

    def model_for(self, cde: str) -> LocalModelDefinition:
        return self._models_by_cde[cde]

    def model_path(self, model: LocalModelDefinition) -> Path:
        return self.root / model.relative_path


def load_model_catalog(config_path: Path) -> LocalModelCatalog:
    """Validate the complete JSON file before any model can be loaded."""
    root = config_path.parent.resolve()
    payload = _read_json(config_path)
    if set(payload) != {"models"}:
        raise LocalModelConfigurationError("Local model file must contain only 'models'")
    raw_models = payload["models"]
    if not isinstance(raw_models, list) or not raw_models:
        raise LocalModelConfigurationError("Local model file must contain at least one model")

    models: list[LocalModelDefinition] = []
    paths: set[Path] = set()
    models_by_cde: dict[str, LocalModelDefinition] = {}
    for index, raw_model in enumerate(raw_models):
        model = _model_from_json(root, raw_model, index)
        if model.relative_path in paths:
            raise LocalModelConfigurationError(f"Duplicate local model path: {model.relative_path}")
        paths.add(model.relative_path)
        for cde in model.cdes:
            previous = models_by_cde.get(cde)
            if previous is not None:
                raise LocalModelConfigurationError(
                    f"CDE {cde} is assigned to both {previous.relative_path} and {model.relative_path}"
                )
            models_by_cde[cde] = model
        models.append(model)
    return LocalModelCatalog(
        root=root,
        models=tuple(models),
        _models_by_cde=MappingProxyType(models_by_cde),
    )


def _read_json(config_path: Path) -> dict[str, object]:
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LocalModelConfigurationError(f"Cannot read local model file: {config_path}") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_object_without_duplicate_keys)
    except (json.JSONDecodeError, LocalModelConfigurationError) as exc:
        if isinstance(exc, LocalModelConfigurationError):
            raise
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


def _model_from_json(root: Path, raw_model: object, index: int) -> LocalModelDefinition:
    if not isinstance(raw_model, dict) or set(raw_model) != {"path", "cdes"}:
        raise LocalModelConfigurationError(
            f"Local model at index {index} must contain only 'path' and 'cdes'"
        )
    raw_path = raw_model["path"]
    if not isinstance(raw_path, str) or not raw_path or raw_path.strip() != raw_path:
        raise LocalModelConfigurationError(f"Local model path at index {index} is invalid")
    relative_path = Path(raw_path)
    if relative_path.is_absolute() or relative_path == Path("."):
        raise LocalModelConfigurationError(f"Local model path must be a relative subdirectory: {raw_path}")
    model_path = (root / relative_path).resolve()
    if not model_path.is_relative_to(root):
        raise LocalModelConfigurationError(f"Local model path must stay inside {root}: {raw_path}")
    if not model_path.is_dir():
        raise LocalModelConfigurationError(f"Local model directory does not exist: {model_path}")

    raw_cdes = raw_model["cdes"]
    if not isinstance(raw_cdes, list) or not raw_cdes:
        raise LocalModelConfigurationError(f"Local model CDEs at index {index} must be a non-empty list")
    cdes: list[str] = []
    for raw_cde in raw_cdes:
        if not isinstance(raw_cde, str) or not raw_cde or raw_cde.strip() != raw_cde:
            raise LocalModelConfigurationError(f"Local model CDE at index {index} is invalid")
        if raw_cde in cdes:
            raise LocalModelConfigurationError(f"Duplicate CDE in {raw_path}: {raw_cde}")
        cdes.append(raw_cde)
    return LocalModelDefinition(relative_path=relative_path, cdes=frozenset(cdes))


__all__ = [
    "LocalModelCatalog",
    "LocalModelConfigurationError",
    "LocalModelDefinition",
    "load_model_catalog",
]
