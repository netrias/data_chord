"""Small public boundary for local harmonization inference."""

from pathlib import Path

from src.local_inference.catalog import LocalModelConfigurationError
from src.local_inference.service import (
    LocalInference,
    LocalInferenceError,
    UnsupportedCdeError,
)


def load_local_inference(config_path: "Path", models_root: "Path") -> LocalInference:
    """Create the complete local inference boundary from one checked file."""
    from src.local_inference.catalog import load_model_catalog
    from src.local_inference.transformers_runner import TransformersModelRunner

    catalog = load_model_catalog(config_path, models_root)
    runner = TransformersModelRunner()
    for model in catalog.models:
        runner.check(catalog.model_path(model), load_model=False)
    return LocalInference(catalog, runner)


__all__ = [
    "LocalInference",
    "LocalInferenceError",
    "LocalModelConfigurationError",
    "UnsupportedCdeError",
    "load_local_inference",
]
