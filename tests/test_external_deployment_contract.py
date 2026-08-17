from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra" / "scripts" / "deployment_contract.py"


def test_exact_external_contract_validates_selected_target(tmp_path: Path) -> None:
    # Given: the foundation repository generated one exact non-secret contract.
    contract = tmp_path / "netrias-staging.json"
    contract.write_text(json.dumps(_document()), encoding="utf-8")

    # When: deployment validates the selected target and stage.
    result = _run("validate", contract, "netrias", "staging")

    # Then: validation succeeds.
    assert result.returncode == 0


def test_external_contract_returns_pinned_commit(tmp_path: Path) -> None:
    # Given: the foundation repository generated one exact non-secret contract.
    contract = tmp_path / "netrias-staging.json"
    contract.write_text(json.dumps(_document()), encoding="utf-8")

    # When: deployment reads the selected commit.
    value = _run("get", contract, "application_commit")

    # Then: the full pinned commit is available.
    assert value.stdout.strip() == "83b201050d502a0a391545e3880dba09c354d499"


def test_external_contract_rejects_secret_values(tmp_path: Path) -> None:
    # Given: the contract contains a secret value.
    contract = tmp_path / "unsafe.json"
    contract.write_text(
        json.dumps({**_document(), "netrias_api_key": "secret"}),
        encoding="utf-8",
    )

    # When: deployment validates the unsafe input.
    unsafe = _run("validate", contract, "netrias", "staging")

    # Then: validation stops before any AWS command can run.
    assert unsafe.returncode == 2
    assert "unsupported fields: netrias_api_key" in unsafe.stderr


def test_external_contract_rejects_wrong_selection(tmp_path: Path) -> None:
    # Given: the contract selects staging.
    contract = tmp_path / "wrong-selection.json"
    contract.write_text(json.dumps(_document()), encoding="utf-8")

    # When: deployment requests production.
    wrong_stage = _run("validate", contract, "netrias", "prod")

    # Then: validation stops before any AWS command can run.
    assert wrong_stage.returncode == 2
    assert "selects stage 'staging', not 'prod'" in wrong_stage.stderr


def test_external_contract_rejects_another_stages_state_key(tmp_path: Path) -> None:
    # Given: a valid-looking staging contract points at production state.
    contract = tmp_path / "wrong-state.json"
    contract.write_text(
        json.dumps({**_document(), "state_key": "datachord/netrias/prod/tofu.tfstate"}),
        encoding="utf-8",
    )

    # When: deployment validates the staging contract.
    result = _run("validate", contract, "netrias", "staging")

    # Then: validation stops before OpenTofu can select the wrong state.
    assert result.returncode == 2
    assert "state_key must be 'datachord/netrias/staging/tofu.tfstate'" in result.stderr


def test_external_contract_rejects_govcloud_until_supported(tmp_path: Path) -> None:
    # Given: a contract selects GovCloud, which the application does not support.
    contract = tmp_path / "govcloud.json"
    document = {
        **_document(),
        "aws_partition": "aws-us-gov",
        "aws_region": "us-gov-west-1",
    }
    contract.write_text(json.dumps(document), encoding="utf-8")

    # When: deployment validates the contract.
    result = _run("validate", contract, "netrias", "staging")

    # Then: it stops at the application boundary.
    assert result.returncode == 2
    assert "only the standard AWS partition is supported" in result.stderr


def test_external_contract_rejects_non_github_repository(tmp_path: Path) -> None:
    # Given: CodeBuild source type cannot use the supplied repository host.
    contract = tmp_path / "non-github.json"
    document = {
        **_document(),
        "application_repository_url": "https://git.example.com/netrias/data_chord.git",
    }
    contract.write_text(json.dumps(document), encoding="utf-8")

    # When: deployment validates the contract.
    result = _run("validate", contract, "netrias", "staging")

    # Then: it rejects the repository before OpenTofu runs.
    assert result.returncode == 2
    assert "must be an HTTPS GitHub repository" in result.stderr


def _run(action: str, contract: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), action, str(contract), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _document() -> dict[str, object]:
    return {
        "application_commit": "83b201050d502a0a391545e3880dba09c354d499",
        "application_repository_url": "https://github.com/netrias/data_chord.git",
        "application_role_boundary_arn": (
            "arn:aws:iam::945365518758:policy/datachord-application-role-boundary"
        ),
        "application_role_path": "/application/",
        "aws_partition": "aws",
        "aws_region": "us-east-2",
        "deployer_role_arn": (
            "arn:aws:iam::945365518758:role/foundation/datachord-deployer"
        ),
        "domain_label": "data-chord-staging",
        "expected_account_id": "945365518758",
        "github_app_secret_name": "data-chord/build/github-app",
        "hosted_zone_name": "apps.netrias.com",
        "netrias_api_key_secret_name": "data-chord/staging/netrias-api-key",
        "stage": "staging",
        "state_bucket_name": (
            "netrias-data-chord-tofu-state-945365518758-us-east-2"
        ),
        "state_key": "datachord/netrias/staging/tofu.tfstate",
        "target_slug": "netrias",
    }
