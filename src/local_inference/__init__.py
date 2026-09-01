"""Small public boundary for local harmonization inference."""

from pathlib import Path

from src.local_inference.catalog import LocalModelConfigurationError
from src.local_inference.service import (
    LocalInferenceError,
    LocalTermHarmonizer,
    UnsupportedCdeError,
)


def load_local_term_harmonizer(config_path: "Path", models_root: "Path") -> LocalTermHarmonizer:
    """Create the complete local inference boundary from one checked file."""
    from src.local_inference.catalog import load_model_catalog

    try:
        from src.local_inference.transformers_runner import TransformersModelRunner
    except ModuleNotFoundError as exc:
        if exc.name not in {"torch", "transformers"}:
            raise
        raise LocalInferenceError("Image was not built with local inference support") from exc

    catalog = load_model_catalog(config_path, models_root)
    runner = TransformersModelRunner()
    for model in catalog.models:
        runner.check(catalog.model_path(model), load_model=False)
    return LocalTermHarmonizer(catalog, runner)


__all__ = [
    "LocalInferenceError",
    "LocalModelConfigurationError",
    "LocalTermHarmonizer",
    "UnsupportedCdeError",
    "load_local_term_harmonizer",
]
