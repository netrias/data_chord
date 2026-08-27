"""Validate local model files before application startup or image delivery."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.local_inference.catalog import LocalModelConfigurationError, load_model_catalog
from src.local_inference.service import LocalInferenceError
from src.local_inference.transformers_runner import TransformersModelRunner


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m src.local_inference")
    parser.add_argument("command", choices=("check",))
    parser.add_argument("config", type=Path)
    parser.add_argument("--load-models", action="store_true")
    arguments = parser.parse_args()
    try:
        catalog = load_model_catalog(arguments.config)
        runner = TransformersModelRunner()
        for model in catalog.models:
            model_path = catalog.model_path(model)
            architecture = runner.check(model_path, load_model=arguments.load_models)
            print(f"{model.relative_path}: {architecture}; CDEs={len(model.cdes)}")
    except (LocalModelConfigurationError, LocalInferenceError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"Checked {len(catalog.models)} local models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
