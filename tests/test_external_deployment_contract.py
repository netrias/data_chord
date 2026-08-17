from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra" / "scripts" / "deployment_contract.py"


def test_exact_external_contract_returns_selected_values(tmp_path: Path) -> None:
    # Given: the foundation repository generated one exact non-secret contract.
    contract = tmp_path / "netrias-staging.json"
    contract.write_text(json.dumps(_document()), encoding="utf-8")
    # When: deployment validates the selected target and stage.
    result = _run("validate", contract, "netrias", "staging")
    value = _run("get", contract, "application_commit")
    # Then: validation succeeds and the full pinned commit is available.
    assert result.returncode == 0
    assert value.stdout.strip() == "83b201050d502a0a391545e3880dba09c354d499"


def test_external_contract_rejects_secret_values_and_wrong_selection(tmp_path: Path) -> None:
    # Given: one file contains a secret value and another selection is requested.
    contract = tmp_path / "unsafe.json"
    contract.write_text(json.dumps({**_document(), "netrias_api_key": "secret"}), encoding="utf-8")
    # When: deployment validates either unsafe input or a wrong stage.
    unsafe = _run("validate", contract, "netrias", "staging")
    contract.write_text(json.dumps(_document()), encoding="utf-8")
    wrong_stage = _run("validate", contract, "netrias", "prod")
    # Then: both cases stop before any AWS command can run.
    assert unsafe.returncode == 2
    assert "unsupported fields: netrias_api_key" in unsafe.stderr
    assert wrong_stage.returncode == 2
    assert "selects stage 'staging', not 'prod'" in wrong_stage.stderr


def test_external_contract_rejects_unsafe_deployment_values(tmp_path: Path) -> None:
    # Given: each contract value would select an unsafe deployment boundary.
    cases = (
        (
            {
                "aws_partition": "aws-us-gov",
                "aws_region": "us-gov-west-1",
                "application_role_boundary_arn": (
                    "arn:aws-us-gov:iam::945365518758:policy/"
                    "datachord-application-role-boundary"
                ),
                "deployer_role_arn": (
                    "arn:aws-us-gov:iam::945365518758:role/foundation/"
                    "datachord-deployer"
                ),
            },
            "does not support GovCloud",
        ),
        (
            {"state_key": "datachord/netrias/prod/tofu.tfstate"},
            "state_key must be 'datachord/netrias/staging/tofu.tfstate'",
        ),
        (
            {
                "application_repository_url": (
                    "https://user:token@github.com/netrias/data_chord.git"
                )
            },
            "must not contain credentials",
        ),
        (
            {
                "deployer_role_arn": (
                    "arn:aws:iam::945365518758:policy/datachord-deployer"
                )
            },
            "deployer_role_arn must be an IAM role",
        ),
        (
            {
                "application_role_boundary_arn": (
                    "arn:aws:iam::945365518758:role/datachord-boundary"
                )
            },
            "application_role_boundary_arn must be an IAM policy",
        ),
    )

    for changes, message in cases:
        contract = tmp_path / "unsafe.json"
        contract.write_text(
            json.dumps({**_document(), **changes}), encoding="utf-8"
        )

        # When: deployment validates the generated contract.
        result = _run("validate", contract, "netrias", "staging")

        # Then: it stops before any AWS command can run.
        assert result.returncode == 2
        assert message in result.stderr


def _run(action: str, contract: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), action, str(contract), *arguments], check=False, capture_output=True, text=True
    )


def _document() -> dict[str, object]:
    return {
        "application_commit": "83b201050d502a0a391545e3880dba09c354d499",
        "application_repository_url": "https://github.com/netrias/data_chord.git",
        "application_role_boundary_arn": "arn:aws:iam::945365518758:policy/datachord-application-role-boundary",
        "application_role_path": "/application/",
        "aws_partition": "aws",
        "aws_region": "us-east-2",
        "deployer_role_arn": "arn:aws:iam::945365518758:role/foundation/datachord-deployer",
        "domain_label": "data-chord-staging",
        "expected_account_id": "945365518758",
        "github_app_secret_name": "data-chord/build/github-app",
        "hosted_zone_name": "apps.netrias.com",
        "netrias_api_key_secret_name": "data-chord/staging/netrias-api-key",
        "stage": "staging",
        "state_bucket_name": "netrias-data-chord-tofu-state-945365518758-us-east-2",
        "state_key": "datachord/netrias/staging/tofu.tfstate",
        "target_slug": "netrias",
    }
