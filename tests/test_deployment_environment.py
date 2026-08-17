from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra" / "scripts" / "environment.py"


def test_environment_derives_the_deployment_boundary(tmp_path: Path) -> None:
    # Given a checked-in environment that copies the foundation names.
    environment = tmp_path / "staging.json"
    environment.write_text(json.dumps(_document()), encoding="utf-8")

    # When the deployment reads its derived values and OpenTofu variables.
    state_key = _run("get", environment, "netrias", "staging", "state_key")
    partition = _run("get", environment, "netrias", "staging", "partition")
    variables = _run("tofu-vars", environment, "netrias", "staging")

    # Then it selects the canonical state namespace and complete application input.
    assert state_key.stdout.strip() == "datachord/netrias/staging/tofu.tfstate"
    assert partition.stdout.strip() == "aws"
    assert json.loads(variables.stdout) == {
        "application_repository_url": "https://github.com/netrias/data_chord.git",
        "application_role_boundary_arn": (
            "arn:aws:iam::945365518758:policy/datachord-application-role-boundary"
        ),
        "application_role_path": "/application/",
        "aws_region": "us-east-2",
        "deployment_target": "netrias",
        "domain_label": "data-chord-staging",
        "environment": "staging",
        "expected_account_id": "945365518758",
        "github_app_secret_name": "data-chord/build/github-app",
        "hosted_zone_name": "apps.netrias.com",
    }


def test_environment_rejects_bdf_before_reading_a_file(tmp_path: Path) -> None:
    # Given no BDF environment file exists.
    environment = tmp_path / "missing.json"
    assert not environment.exists()

    # When the new deployment flow receives the legacy BDF target.
    result = _run("validate", environment, "bdf", "staging")

    # Then it rejects the manual target before it attempts to read configuration.
    assert result.returncode == 2
    assert "BDF uses legacy state" in result.stderr
    assert "does not exist" not in result.stderr


def test_environment_rejects_unknown_fields_and_wrong_foundation_names(
    tmp_path: Path,
) -> None:
    # Given one environment adds a second source of truth and another changes the role name.
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(
        json.dumps({**_document(), "state_key": "another/path.tfstate"}),
        encoding="utf-8",
    )
    wrong_role = tmp_path / "wrong-role.json"
    wrong_role.write_text(
        json.dumps(
            {
                **_document(),
                "deployer_role_arn": (
                    "arn:aws:iam::945365518758:role/datachord-deployer"
                ),
            }
        ),
        encoding="utf-8",
    )

    # When each environment is validated.
    extra_field = _run("validate", unsafe, "netrias", "staging")
    mismatched_role = _run("validate", wrong_role, "netrias", "staging")

    # Then both stop with the exact violated boundary.
    assert extra_field.returncode == 2
    assert "unsupported environment fields: state_key" in extra_field.stderr
    assert mismatched_role.returncode == 2
    assert "role/foundation/datachord-deployer" in mismatched_role.stderr


def test_environment_rejects_reference_data_as_deployment_input(tmp_path: Path) -> None:
    # Given an environment file tries to couple a data export to deployment.
    environment = tmp_path / "with-reference-data.json"
    document = {
        **_document(),
        "reference_data": {"source": "private-export.json", "sha256": "a" * 64},
    }
    environment.write_text(json.dumps(document), encoding="utf-8")

    # When the deployment environment is validated.
    result = _run("validate", environment, "netrias", "staging")

    # Then the strict deployment contract rejects the separate data operation.
    assert result.returncode == 2
    assert "unsupported environment fields: reference_data" in result.stderr


def test_environment_rejects_govcloud_until_authentication_is_replaced(
    tmp_path: Path,
) -> None:
    # Given an environment selects GovCloud with partition-correct foundation ARNs.
    govcloud = tmp_path / "govcloud.json"
    gov_document = {
        **_document(),
        "region": "us-gov-west-1",
        "deployer_role_arn": (
            "arn:aws-us-gov:iam::945365518758:role/foundation/datachord-deployer"
        ),
        "application_role_boundary_arn": (
            "arn:aws-us-gov:iam::945365518758:policy/"
            "datachord-application-role-boundary"
        ),
    }
    govcloud.write_text(json.dumps(gov_document), encoding="utf-8")

    # When the environment is validated.
    unsupported = _run("validate", govcloud, "core", "staging")

    # Then deployment stops before AWS is used.
    assert unsupported.returncode == 2
    assert "does not support GovCloud" in unsupported.stderr


def _run(
    action: str, environment: Path, target: str, stage: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "python3",
            str(SCRIPT),
            action,
            str(environment),
            target,
            stage,
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _document() -> dict[str, object]:
    return {
        "account_id": "945365518758",
        "region": "us-east-2",
        "state_bucket_name": "netrias-datachord-state-945365518758-us-east-2",
        "deployer_role_arn": (
            "arn:aws:iam::945365518758:role/foundation/datachord-deployer"
        ),
        "application_role_boundary_arn": (
            "arn:aws:iam::945365518758:policy/datachord-application-role-boundary"
        ),
        "application_role_path": "/application/",
        "domain_name": "data-chord-staging.apps.netrias.com",
        "hosted_zone_name": "apps.netrias.com",
        "application_repository_url": "https://github.com/netrias/data_chord.git",
        "github_app_secret_name": "data-chord/build/github-app",
    }
