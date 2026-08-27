"""Torch and Hugging Face implementation hidden behind local inference."""

from __future__ import annotations

import gc
import logging
import time
from pathlib import Path
from typing import cast

import torch
from transformers import (
    AutoConfig,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    PreTrainedModel,
    PreTrainedTokenizerBase,
)

from src.domain.harmonization import MatchFidelity
from src.local_inference.service import (
    LocalInferenceError,
    LocalInferenceRequest,
    LocalInferenceResult,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 8
_STRONG_CONFIDENCE = 0.9
_NO_MATCH = "NO_MATCH"


class TransformersModelRunner:
    """Load, use, and release one standard sequence-classification model."""

    def harmonize(
        self,
        model_path: Path,
        requests: tuple[LocalInferenceRequest, ...],
    ) -> tuple[LocalInferenceResult, ...]:
        if not requests:
            return ()
        started_at = time.perf_counter()
        device = _inference_device()
        tokenizer: PreTrainedTokenizerBase | None = None
        model: PreTrainedModel | None = None
        try:
            tokenizer = cast(
                PreTrainedTokenizerBase,
                # Bandit cannot infer that the checked path and local-only flag block Hub downloads.
                AutoTokenizer.from_pretrained(  # nosec B615
                    model_path,
                    local_files_only=True,
                    trust_remote_code=False,
                ),
            )
            model = cast(
                PreTrainedModel,
                AutoModelForSequenceClassification.from_pretrained(  # nosec B615
                    model_path,
                    local_files_only=True,
                    trust_remote_code=False,
                ),
            )
            torch.nn.Module.to(model, device)
            model.eval()
            loaded_at = time.perf_counter()
            results = _run_batches(model, tokenizer, requests, device)
            finished_at = time.perf_counter()
            logger.info(
                "Completed local model inference",
                extra={
                    "model_path": str(model_path),
                    "architecture": model.config.model_type,
                    "device": device.type,
                    "terms": len(requests),
                    "load_ms": round((loaded_at - started_at) * 1000, 1),
                    "inference_ms": round((finished_at - loaded_at) * 1000, 1),
                },
            )
            return results
        except LocalInferenceError:
            raise
        except Exception as exc:
            raise LocalInferenceError(f"Local model failed: {model_path}: {exc}") from exc
        finally:
            del model
            del tokenizer
            _release_device_memory(device)

    def check(self, model_path: Path, *, load_model: bool) -> str:
        """Check local metadata, and optionally prove that all weights load."""
        try:
            # Bandit cannot infer that the checked path and local-only flag block Hub downloads.
            config = AutoConfig.from_pretrained(  # nosec B615
                model_path,
                local_files_only=True,
                trust_remote_code=False,
            )
            AutoTokenizer.from_pretrained(  # nosec B615
                model_path,
                local_files_only=True,
                trust_remote_code=False,
            )
            if load_model:
                model = AutoModelForSequenceClassification.from_pretrained(  # nosec B615
                    model_path,
                    local_files_only=True,
                    trust_remote_code=False,
                )
                del model
                _release_device_memory(_inference_device())
            return config.model_type
        except Exception as exc:
            raise LocalInferenceError(f"Local model check failed: {model_path}: {exc}") from exc


def _run_batches(
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    requests: tuple[LocalInferenceRequest, ...],
    device: torch.device,
) -> tuple[LocalInferenceResult, ...]:
    results: list[LocalInferenceResult] = []
    model_labels = _model_labels(model)
    for start in range(0, len(requests), _BATCH_SIZE):
        batch = requests[start : start + _BATCH_SIZE]
        encoded = tokenizer(
            [_model_input(request) for request in batch],
            padding=True,
            truncation=True,
            return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            logits = model(**encoded).logits.detach().to("cpu")
        results.extend(
            _decode_result(model_labels, request, row)
            for request, row in zip(batch, logits, strict=True)
        )
    return tuple(results)


def _model_input(request: LocalInferenceRequest) -> str:
    return f"CDE: {request.cde}\nSource value: {request.source_value}"


def _model_labels(model: PreTrainedModel) -> dict[int, str]:
    raw_labels = model.config.id2label
    if raw_labels is None:
        raise LocalInferenceError("Local model has no output labels")
    return {int(index): label for index, label in raw_labels.items()}


def _decode_result(
    id_to_label: dict[int, str],
    request: LocalInferenceRequest,
    logits: torch.Tensor,
) -> LocalInferenceResult:
    candidates = [
        index
        for index, label in id_to_label.items()
        if label == _NO_MATCH or label in request.permissible_values
    ]
    if not candidates:
        raise LocalInferenceError(f"Local model has no labels for CDE {request.cde}")
    probabilities = torch.softmax(logits, dim=-1)
    selected_index = max(candidates, key=lambda index: float(probabilities[index]))
    selected_label = id_to_label[selected_index]
    if selected_label == _NO_MATCH:
        return LocalInferenceResult(None, MatchFidelity.NONE)
    confidence = float(probabilities[selected_index])
    fidelity = MatchFidelity.STRONG if confidence >= _STRONG_CONFIDENCE else MatchFidelity.PARTIAL
    return LocalInferenceResult(selected_label, fidelity)


def _inference_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _release_device_memory(device: torch.device) -> None:
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    elif device.type == "mps":
        torch.mps.empty_cache()


__all__ = ["TransformersModelRunner"]
