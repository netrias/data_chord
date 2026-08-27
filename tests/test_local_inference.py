"""Local model configuration and inference behavior."""

from __future__ import annotations

import builtins
import json
import math
from pathlib import Path
from typing import cast

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
from src.integrations.harmonize import TermHarmonizationRequest, TermHarmonizationResponse
from src.local_inference import load_local_term_harmonizer
from src.local_inference.catalog import (
    LocalModelConfig,
    LocalModelConfigurationError,
    load_model_catalog,
)
from src.local_inference.service import (
    LocalInferenceError,
    LocalTermHarmonizer,
)
from src.local_inference.transformers_runner import TransformersModelRunner, _decode_result


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
    return TermHarmonizationRequest(
        cde,
        source_value,
        tuple(sorted(permissible_values)),
        f"Target CDE: {cde}",
    )


def _write_tiny_transformers_model(
    model_path: Path,
    architecture: str,
    *,
    include_padding_token: bool = True,
) -> None:
    vocabulary = {"[UNK]": 0, "CDE": 1, "sex": 2, "Source": 3, "value": 4, "m": 5}
    if include_padding_token:
        vocabulary["[PAD]"] = len(vocabulary)
    raw_tokenizer = Tokenizer(WordLevel(vocabulary, unk_token="[UNK]"))
    raw_tokenizer.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=raw_tokenizer,
        pad_token="[PAD]" if include_padding_token else None,
        unk_token="[UNK]",
        model_max_length=32,
    )
    pad_token_id = cast(int | None, tokenizer.pad_token_id)
    labels = {0: "Male", 1: "Female"}
    if architecture == "gpt2":
        gpt2_config = GPT2Config(
            vocab_size=len(vocabulary),
            n_positions=32,
            n_embd=8,
            n_layer=1,
            n_head=1,
            pad_token_id=pad_token_id,
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
            pad_token_id=pad_token_id,
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
    ) -> tuple[TermHarmonizationResponse, ...]:
        self.calls.append((model_path, model_config, requests))
        return tuple(
            TermHarmonizationResponse(
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

    # When the file is validated.
    with pytest.raises(LocalModelConfigurationError) as exc_info:
        load_model_catalog(config_path, tmp_path)

    # Then the error identifies both conflicting paths.
    assert str(exc_info.value) == "CDE cell_type is assigned to both gpt2-v1 and biobert-v1"


def test_catalog_rejects_a_model_path_outside_the_config_directory(tmp_path: Path) -> None:
    # Given a model path attempts to leave the mounted model directory.
    config_path = _write_config(tmp_path, [{"path": "../outside", "cdes": ["cell_type"]}])

    # When the file is validated.
    with pytest.raises(LocalModelConfigurationError) as exc_info:
        load_model_catalog(config_path, tmp_path)

    # Then the escaped path is rejected at the boundary.
    assert "must stay inside" in str(exc_info.value)


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

    # When the JSON file is converted.
    with pytest.raises(LocalModelConfigurationError) as exc_info:
        load_model_catalog(config_path, tmp_path)

    # Then typed configuration rejects the setting.
    assert expected_error in str(exc_info.value)


def test_catalog_rejects_non_finite_confidence(tmp_path: Path) -> None:
    # Given Python's JSON parser accepts a non-finite confidence value.
    (tmp_path / "gpt2-v1").mkdir()
    config_path = _write_config(
        tmp_path,
        [{"path": "gpt2-v1", "cdes": ["cell_type"], "strong_confidence": math.nan}],
    )

    # When the configuration is converted.
    with pytest.raises(LocalModelConfigurationError) as exc_info:
        load_model_catalog(config_path, tmp_path)

    # Then the invalid threshold is rejected.
    assert "strong_confidence" in str(exc_info.value)


def test_local_inference_rejects_a_model_without_configuration_or_tokenizer(tmp_path: Path) -> None:
    # Given the model file points to an existing but empty directory.
    (tmp_path / "empty-model").mkdir()
    config_path = _write_config(tmp_path, [{"path": "empty-model", "cdes": ["cell_type"]}])

    # When application wiring loads local inference.
    with pytest.raises(LocalInferenceError) as exc_info:
        load_local_term_harmonizer(config_path, tmp_path)

    # Then it rejects the unusable model at startup.
    assert "Local model check failed" in str(exc_info.value)


def test_local_inference_reports_an_image_without_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given the image does not contain the optional local inference runtime.
    real_import = builtins.__import__

    def import_without_torch(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "src.local_inference.transformers_runner":
            raise ModuleNotFoundError("No module named 'torch'", name="torch")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_torch)

    # When local inference is selected.
    with pytest.raises(LocalInferenceError) as exc_info:
        load_local_term_harmonizer(tmp_path / "local_models.json", tmp_path / "models")

    # Then startup reports the missing image capability in operator language.
    assert str(exc_info.value) == "Image was not built with local inference support"


def test_local_inference_rejects_a_model_without_a_padding_token(tmp_path: Path) -> None:
    # Given a complete GPT-2 export whose tokenizer cannot pad a batch.
    model_path = tmp_path / "gpt2-without-padding"
    _write_tiny_transformers_model(
        model_path,
        "gpt2",
        include_padding_token=False,
    )
    config_path = _write_config(tmp_path, [{"path": model_path.name, "cdes": ["sex"]}])

    # When application wiring checks the model before startup.
    with pytest.raises(LocalInferenceError) as exc_info:
        load_local_term_harmonizer(config_path, tmp_path)

    # Then Stage 3 cannot receive a model that would fail during batch padding.
    assert "padding token" in str(exc_info.value)


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
    inference = LocalTermHarmonizer(catalog, runner)
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
    inference = LocalTermHarmonizer(catalog, runner)

    # When local inference receives the unsupported request.
    with pytest.raises(KeyError) as exc_info:
        inference.harmonize((_request("specimen_type", "tumor", frozenset({"Tissue"})),))

    # Then the error identifies the CDE and no model is loaded.
    assert "specimen_type" in str(exc_info.value)
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
    assert result == (TermHarmonizationResponse("Male", MatchFidelity.PARTIAL),)


def test_transformers_runner_does_not_replace_a_shared_model_prediction_with_another_label() -> None:
    # Given one model serves two CDEs and strongly predicts a label from the other CDE.
    request = _request("diagnosis", "sample", frozenset({"Allowed diagnosis"}))
    labels = {0: "Allowed diagnosis", 1: "Other CDE value", 2: "NO_MATCH"}
    logits = torch.tensor([0.0, 10.0, -1.0])

    # When the output is decoded for the current CDE.
    result = _decode_result(labels, request, logits, strong_confidence=0.9)

    # Then it returns no match instead of changing the winner to a weak allowed label.
    assert result == TermHarmonizationResponse(None, MatchFidelity.NONE)
