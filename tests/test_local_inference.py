"""Local model configuration and inference behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import (
    BertConfig,
    BertForSequenceClassification,
    GPT2Config,
    GPT2ForSequenceClassification,
    PreTrainedModel,
    PreTrainedTokenizerFast,
)

from src.domain.harmonization import MatchFidelity
from src.integrations.harmonize import TermHarmonizationRequest, TermHarmonizationResult
from src.local_inference import load_local_inference
from src.local_inference.catalog import (
    LocalModelConfig,
    LocalModelConfigurationError,
    load_model_catalog,
)
from src.local_inference.service import (
    LocalInference,
    LocalInferenceError,
)
from src.local_inference.transformers_runner import TransformersModelRunner


def _write_config(root: Path, models: list[dict[str, object]]) -> Path:
    config_path = root / "local_models.json"
    complete_models = [{"batch_size": 8, "strong_confidence": 0.9, **model} for model in models]
    config_path.write_text(json.dumps({"models": complete_models}), encoding="utf-8")
    return config_path


def _request(
    cde: str,
    source_value: str,
    permissible_values: frozenset[str],
) -> TermHarmonizationRequest:
    return TermHarmonizationRequest(cde, source_value, permissible_values, f"Target CDE: {cde}")


def _write_tiny_transformers_model(model_path: Path, architecture: str) -> None:
    vocabulary = {"[PAD]": 0, "[UNK]": 1, "CDE": 2, "sex": 3, "Source": 4, "value": 5, "m": 6}
    raw_tokenizer = Tokenizer(WordLevel(vocabulary, unk_token="[UNK]"))
    raw_tokenizer.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=raw_tokenizer,
        pad_token="[PAD]",
        unk_token="[UNK]",
        model_max_length=32,
    )
    labels = {0: "Male", 1: "Female"}
    if architecture == "gpt2":
        gpt2_config = GPT2Config(
            vocab_size=len(vocabulary),
            n_positions=32,
            n_embd=8,
            n_layer=1,
            n_head=1,
            pad_token_id=0,
        )
        gpt2_config.id2label = labels
        gpt2_config.label2id = {label: index for index, label in labels.items()}
        model: PreTrainedModel = GPT2ForSequenceClassification(gpt2_config)
    else:
        bert_config = BertConfig(
            vocab_size=len(vocabulary),
            hidden_size=8,
            num_hidden_layers=1,
            num_attention_heads=1,
            intermediate_size=16,
            pad_token_id=0,
        )
        bert_config.id2label = labels
        bert_config.label2id = {label: index for index, label in labels.items()}
        model = BertForSequenceClassification(bert_config)
    for parameter in model.parameters():
        torch.nn.init.zeros_(parameter)
    model.save_pretrained(model_path)
    tokenizer.save_pretrained(model_path)


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, LocalModelConfig, tuple[TermHarmonizationRequest, ...]]] = []

    def harmonize(
        self,
        model_path: Path,
        model_config: LocalModelConfig,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResult, ...]:
        self.calls.append((model_path, model_config, requests))
        return tuple(
            TermHarmonizationResult(
                matched_value=sorted(request.permissible_values)[0],
                match_fidelity=MatchFidelity.STRONG,
            )
            for request in requests
        )


def test_catalog_uses_each_model_path_as_its_identity(tmp_path: Path) -> None:
    # Given one file assigns several PHYSIC CDE keys to two model directories.
    (tmp_path / "models" / "gpt2-sex-v1").mkdir(parents=True)
    (tmp_path / "models" / "biobert-tissue-v1").mkdir()
    config_path = _write_config(
        tmp_path / "models",
        [
            {"path": "gpt2-sex-v1", "cdes": ["sex", "gender_identity"]},
            {"path": "biobert-tissue-v1", "cdes": ["specimen_type"]},
        ],
    )

    # When the local inference boundary loads the file.
    catalog = load_model_catalog(config_path, tmp_path / "models")

    # Then paths are the model identities and the CDE lookup is derived once.
    assert catalog.supported_cdes == frozenset({"sex", "gender_identity", "specimen_type"})
    assert catalog.model_for("gender_identity").relative_path == Path("gpt2-sex-v1")
    assert catalog.model_for("specimen_type").relative_path == Path("biobert-tissue-v1")
    assert catalog.model_for("specimen_type").batch_size == 8
    assert catalog.model_for("specimen_type").strong_confidence == 0.9


def test_catalog_rejects_one_cde_assigned_to_two_models(tmp_path: Path) -> None:
    # Given two existing model directories claim the same CDE.
    (tmp_path / "gpt2-v1").mkdir()
    (tmp_path / "biobert-v1").mkdir()
    config_path = _write_config(
        tmp_path,
        [
            {"path": "gpt2-v1", "cdes": ["cell_type"]},
            {"path": "biobert-v1", "cdes": ["cell_type"]},
        ],
    )

    # When the file is validated, then the error identifies both conflicting paths.
    with pytest.raises(
        LocalModelConfigurationError,
        match=r"cell_type.*gpt2-v1.*biobert-v1",
    ):
        load_model_catalog(config_path, tmp_path)


def test_catalog_rejects_a_model_path_outside_the_config_directory(tmp_path: Path) -> None:
    # Given a model path attempts to leave the mounted model directory.
    config_path = _write_config(tmp_path, [{"path": "../outside", "cdes": ["cell_type"]}])

    # When the file is validated, then the escaped path is rejected at the boundary.
    with pytest.raises(LocalModelConfigurationError, match="must stay inside"):
        load_model_catalog(config_path, tmp_path)


@pytest.mark.parametrize(
    ("setting", "value", "expected_error"),
    [
        ("batch_size", 0, "batch_size"),
        ("strong_confidence", 1.1, "strong_confidence"),
    ],
)
def test_catalog_rejects_invalid_model_execution_settings(
    tmp_path: Path,
    setting: str,
    value: object,
    expected_error: str,
) -> None:
    # Given one model entry has an unsafe execution setting.
    (tmp_path / "gpt2-v1").mkdir()
    model = {
        "path": "gpt2-v1",
        "cdes": ["cell_type"],
        setting: value,
    }
    config_path = _write_config(tmp_path, [model])

    # When the JSON file is converted, then typed configuration rejects the setting.
    with pytest.raises(LocalModelConfigurationError, match=expected_error):
        load_model_catalog(config_path, tmp_path)


def test_local_inference_rejects_a_model_without_configuration_or_tokenizer(tmp_path: Path) -> None:
    # Given the model file points to an existing but empty directory.
    (tmp_path / "empty-model").mkdir()
    config_path = _write_config(tmp_path, [{"path": "empty-model", "cdes": ["cell_type"]}])

    # When application wiring loads local inference, then it rejects the unusable model at startup.
    with pytest.raises(LocalInferenceError, match="Local model check failed"):
        load_local_inference(config_path, tmp_path)


def test_local_inference_groups_requests_by_model_and_restores_request_order(tmp_path: Path) -> None:
    # Given three requests where two CDEs share one model.
    (tmp_path / "gpt2-v1").mkdir()
    (tmp_path / "biobert-v1").mkdir()
    catalog = load_model_catalog(
        _write_config(
            tmp_path,
            [
                {"path": "gpt2-v1", "cdes": ["sex", "gender_identity"]},
                {"path": "biobert-v1", "cdes": ["specimen_type"]},
            ],
        ),
        tmp_path,
    )
    runner = _RecordingRunner()
    inference = LocalInference(catalog, runner)
    requests = (
        _request("specimen_type", "tumor", frozenset({"Tissue"})),
        _request("sex", "m", frozenset({"Male"})),
        _request("gender_identity", "woman", frozenset({"Female"})),
    )

    # When all requests are harmonized in one call.
    results = inference.harmonize(requests)

    # Then each model runs once and results retain the caller's request order.
    assert [(path.name, [request.cde for request in grouped]) for path, _model, grouped in runner.calls] == [
        ("gpt2-v1", ["sex", "gender_identity"]),
        ("biobert-v1", ["specimen_type"]),
    ]
    assert [result.matched_value for result in results] == ["Tissue", "Male", "Female"]


def test_local_inference_rejects_an_unassigned_cde_before_loading_a_model(tmp_path: Path) -> None:
    # Given one configured model and a request for a different CDE.
    (tmp_path / "gpt2-v1").mkdir()
    catalog = load_model_catalog(
        _write_config(tmp_path, [{"path": "gpt2-v1", "cdes": ["sex"]}]),
        tmp_path,
    )
    runner = _RecordingRunner()
    inference = LocalInference(catalog, runner)

    # When local inference receives the unsupported request, then no model is loaded.
    with pytest.raises(KeyError, match="specimen_type"):
        inference.harmonize((_request("specimen_type", "tumor", frozenset({"Tissue"})),))
    assert runner.calls == []


@pytest.mark.parametrize("architecture", ["gpt2", "bert"])
def test_transformers_runner_loads_real_supported_architectures_and_returns_an_allowed_label(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    architecture: str,
) -> None:
    # Given a complete local GPT-2 or BERT sequence-classification export.
    model_path = tmp_path / architecture
    _write_tiny_transformers_model(model_path, architecture)
    monkeypatch.setattr(
        "src.local_inference.transformers_runner._inference_device",
        lambda: torch.device("cpu"),
    )
    runner = TransformersModelRunner()
    request = _request("sex", "m", frozenset({"Male", "Female"}))
    model_config = LocalModelConfig(Path(architecture), frozenset({"sex"}), 8, 0.9)

    # When metadata, weights, tokenization, batching, and decoding are exercised.
    detected_architecture = runner.check(model_path, load_model=True)
    result = runner.harmonize(model_path, model_config, (request,))

    # Then the auto-loader detects the real architecture and returns one allowed model label.
    assert detected_architecture == architecture
    assert result == (TermHarmonizationResult("Male", MatchFidelity.PARTIAL),)
