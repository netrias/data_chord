"""Build and prove the complete local-model container workflow."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import socket
import subprocess  # nosec B404
import time
from collections.abc import Mapping, Sequence
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import torch
from netrias_client import NetriasClient
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

_API_KEY = "local-inference-container-test-key"
# These are tokenizer control values, not secrets.
_PADDING_TOKEN = "[PAD]"  # nosec B105
_READY_ATTEMPTS = 120
_UNKNOWN_TOKEN = "[UNK]"  # nosec B105


def _run(
    command: Sequence[str],
    *,
    working_directory: Path,
    environment: Mapping[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    # Callers provide fixed local verification commands.
    return subprocess.run(  # nosec B603
        command,
        cwd=working_directory,
        env=environment,
        check=True,
        capture_output=capture_output,
        text=True,
    )


def _run_unchecked(
    command: Sequence[str],
    *,
    working_directory: Path,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    output = subprocess.DEVNULL if quiet else None
    # Callers provide fixed cleanup and diagnostic commands.
    return subprocess.run(  # nosec B603
        command,
        cwd=working_directory,
        check=False,
        stdout=output,
        stderr=output,
        text=True,
    )


def _github_token(repository_root: Path) -> str:
    configured = os.environ.get("GITHUB_TOKEN", "").strip()
    if configured:
        return configured
    completed = _run(
        ["gh", "auth", "token"],
        working_directory=repository_root,
        capture_output=True,
    )
    token = completed.stdout.strip()
    if not token:
        raise RuntimeError("GitHub authentication is required to build the image")
    return token


def _build_image(
    repository_root: Path,
    image: str,
    *,
    include_local_inference: bool,
) -> None:
    environment = dict(os.environ)
    environment["DATA_CHORD_TEST_GITHUB_TOKEN"] = _github_token(repository_root)
    command = [
        "docker",
        "build",
        "--secret",
        "id=github_token,env=DATA_CHORD_TEST_GITHUB_TOKEN",
        "--build-arg",
        f"DATA_CHORD_INCLUDE_LOCAL_INFERENCE={str(include_local_inference).lower()}",
        "--tag",
        image,
        str(repository_root),
    ]
    try:
        _run(command, working_directory=repository_root, environment=environment)
    finally:
        environment.pop("DATA_CHORD_TEST_GITHUB_TOKEN", None)


def _check_image_dependencies(
    repository_root: Path,
    image: str,
    *,
    local_inference_expected: bool,
) -> None:
    expected = "True" if local_inference_expected else "False"
    program = (
        "import importlib.util; "
        f"assert (importlib.util.find_spec('torch') is not None) is {expected}; "
        f"assert (importlib.util.find_spec('transformers') is not None) is {expected}"
    )
    _run(
        ["docker", "run", "--rm", image, "python", "-c", program],
        working_directory=repository_root,
    )


def _write_model(model_path: Path, architecture: str, labels: tuple[str, ...]) -> None:
    vocabulary = {_PADDING_TOKEN: 0, _UNKNOWN_TOKEN: 1, "CDE": 2, "Source": 3, "value": 4, "lung": 5, "tumor": 6}
    raw_tokenizer = Tokenizer(WordLevel(vocabulary, unk_token=_UNKNOWN_TOKEN))
    raw_tokenizer.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=raw_tokenizer,
        pad_token=_PADDING_TOKEN,
        unk_token=_UNKNOWN_TOKEN,
        model_max_length=32,
    )
    label_map = dict(enumerate(labels))
    if architecture == "gpt2":
        config = GPT2Config(
            vocab_size=len(vocabulary),
            n_positions=32,
            n_embd=8,
            n_layer=1,
            n_head=1,
            pad_token_id=0,
            bos_token_id=None,
            eos_token_id=None,
        )
        config.id2label = label_map
        config.label2id = {label: index for index, label in label_map.items()}
        model: PreTrainedModel = GPT2ForSequenceClassification(config)
    elif architecture == "bert":
        config = BertConfig(
            vocab_size=len(vocabulary),
            hidden_size=8,
            num_hidden_layers=1,
            num_attention_heads=1,
            intermediate_size=16,
            pad_token_id=0,
        )
        config.id2label = label_map
        config.label2id = {label: index for index, label in label_map.items()}
        model = BertForSequenceClassification(config)
    else:
        raise ValueError(f"Unsupported test architecture: {architecture}")
    for parameter in model.parameters():
        torch.nn.init.zeros_(parameter)
    model.save_pretrained(model_path)
    tokenizer.save_pretrained(model_path)


def _write_local_models(test_root: Path) -> tuple[Path, Path]:
    models_root = test_root / "models"
    models_root.mkdir()
    _write_model(models_root / "gpt2-diagnosis", "gpt2", ("Lung Cancer", "NO_MATCH"))
    _write_model(models_root / "bert-specimen", "bert", ("Tissue", "NO_MATCH"))
    config_path = test_root / "local_models.json"
    config_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "path": "gpt2-diagnosis",
                        "cdes": ["primary_diagnosis"],
                        "batch_size": 8,
                        "strong_confidence": 0.9,
                    },
                    {
                        "path": "bert-specimen",
                        "cdes": ["specimen_type"],
                        "batch_size": 8,
                        "strong_confidence": 0.9,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return models_root, config_path


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return cast(int, listener.getsockname()[1])


def _load_reference_data(
    repository_root: Path,
    image: str,
    volume: str,
    reference_path: Path,
) -> None:
    digest = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=volume,src={volume},dst=/data",
            "--mount",
            f"type=bind,src={reference_path},dst=/import/reference.json,readonly",
            image,
            "python",
            "-m",
            "scripts.reference_data",
            "load-sqlite",
            "--input",
            "/import/reference.json",
            "--expected-sha256",
            digest,
            "--database",
            "/data/standards.sqlite",
        ],
        working_directory=repository_root,
    )


def _start_container(
    repository_root: Path,
    image: str,
    container: str,
    volume: str,
    models_root: Path,
    config_path: Path,
    port: int,
) -> None:
    _run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--mount",
            f"type=volume,src={volume},dst=/data",
            "--mount",
            f"type=bind,src={models_root.resolve()},dst=/models,readonly",
            "--mount",
            f"type=bind,src={config_path.resolve()},dst=/app/config/local_models.json,readonly",
            "--env",
            "DATA_CHORD_PROFILE=portable",
            "--env",
            "DATA_CHORD_HARMONIZATION_METHOD=local",
            "--env",
            f"DATA_CHORD_API_KEY={_API_KEY}",
            "--env",
            "AWS_REGION=us-east-2",
            "--publish",
            f"127.0.0.1:{port}:8000",
            image,
        ],
        working_directory=repository_root,
    )


def _wait_until_ready(repository_root: Path, container: str, port: int) -> None:
    for _attempt in range(_READY_ATTEMPTS):
        connection = HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            connection.request("GET", "/healthz")
            if connection.getresponse().status == 200:
                return
        except OSError:
            pass
        finally:
            connection.close()
        running = _run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container],
            working_directory=repository_root,
            capture_output=True,
        )
        if running.stdout.strip() != "true":
            raise RuntimeError("Local inference container stopped before it became healthy")
        time.sleep(0.5)
    raise RuntimeError("Local inference container did not become healthy")


def _run_harmonization(test_root: Path, port: int) -> None:
    source_path = test_root / "source.csv"
    source_path.write_text("diagnosis,specimen\nlung,tumor\nlung,tumor\n", encoding="utf-8")
    output_path = test_root / "harmonized.csv"
    client = NetriasClient(_API_KEY)
    client.configure(harmonization_url=f"http://127.0.0.1:{port}/api", timeout=60)
    result = client.harmonize(
        source_path,
        {
            "column_mappings": [
                {
                    "column_name": "diagnosis",
                    "cde_key": "primary_diagnosis",
                    "cde_id": 1001,
                    "harmonization": "harmonizable",
                    "alternatives": [],
                },
                {
                    "column_name": "specimen",
                    "cde_key": "specimen_type",
                    "cde_id": 1002,
                    "harmonization": "harmonizable",
                    "alternatives": [],
                },
            ]
        },
        "data-chord-demo",
        external_version_number="1.0",
        output_path=output_path,
        use_cache=False,
    )
    if result.status != "succeeded":
        raise AssertionError(f"Local harmonization failed: {result.status}")
    with output_path.open(newline="", encoding="utf-8") as output_file:
        rows = list(csv.reader(output_file))
    expected_rows = [
        ["diagnosis", "specimen"],
        ["Lung Cancer", "Tissue"],
        ["Lung Cancer", "Tissue"],
    ]
    if rows != expected_rows:
        raise AssertionError(f"Unexpected harmonized rows: {rows}")
    if result.manifest_path is None or result.manifest_path.read_bytes()[:4] != b"PAR1":
        raise AssertionError("The harmonization manifest is not Parquet")


def _verify_model_runs(repository_root: Path, container: str) -> None:
    completed = _run(
        ["docker", "logs", container],
        working_directory=repository_root,
        capture_output=True,
    )
    logs = completed.stdout + completed.stderr
    completed_models = logs.count("Completed local model inference")
    if completed_models != 2:
        raise AssertionError(f"Expected two local model runs, found {completed_models}")


def _remove_test_runtime(repository_root: Path, container: str, volume: str) -> None:
    _run_unchecked(
        ["docker", "rm", "--force", container],
        working_directory=repository_root,
        quiet=True,
    )
    _run_unchecked(
        ["docker", "volume", "rm", volume],
        working_directory=repository_root,
        quiet=True,
    )


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    commit = _run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        working_directory=repository_root,
        capture_output=True,
    ).stdout.strip()
    agentic_image = f"data-chord:agentic-{commit}-verify"
    local_image = f"data-chord:local-inference-{commit}-verify"
    container = f"data-chord-local-inference-{os.getpid()}"
    volume = f"data-chord-local-inference-{os.getpid()}"
    _build_image(repository_root, agentic_image, include_local_inference=False)
    _check_image_dependencies(
        repository_root,
        agentic_image,
        local_inference_expected=False,
    )
    _build_image(repository_root, local_image, include_local_inference=True)
    _check_image_dependencies(
        repository_root,
        local_image,
        local_inference_expected=True,
    )

    artifacts_root = Path.home() / ".cache" / "data-chord-container-tests"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="local-inference-", dir=artifacts_root) as temporary_directory:
        test_root = Path(temporary_directory)
        reference_path = test_root / "reference-data.synthetic.json"
        shutil.copyfile(repository_root / "demo" / "reference-data.synthetic.json", reference_path)
        models_root, config_path = _write_local_models(test_root)
        port = _available_port()
        _run(["docker", "volume", "create", volume], working_directory=repository_root)
        try:
            _load_reference_data(repository_root, local_image, volume, reference_path)
            _start_container(
                repository_root,
                local_image,
                container,
                volume,
                models_root,
                config_path,
                port,
            )
            _wait_until_ready(repository_root, container, port)
            _run_harmonization(test_root, port)
            _verify_model_runs(repository_root, container)
        except Exception:
            _run_unchecked(
                ["docker", "logs", container],
                working_directory=repository_root,
            )
            raise
        finally:
            _remove_test_runtime(repository_root, container, volume)

    local_image_id = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", local_image],
        working_directory=repository_root,
        capture_output=True,
    ).stdout.strip()
    print(f"Local inference container workflow passed: {local_image}")
    print(f"Image ID: {local_image_id}")


if __name__ == "__main__":
    main()
