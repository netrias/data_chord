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
        "application_role_boundary_arn": ("arn:aws:iam::945365518758:policy/datachord-application-role-boundary"),
        "application_role_path": "/application/",
        "aws_region": "us-east-2",
        "deployment_target": "netrias",
        "domain_label": "data-chord-staging",
        "environment": "staging",
        "expected_account_id": "945365518758",
        "github_app_secret_name": "data-chord/build/github-app",
        "hosted_zone_name": "apps.netrias.com",
    }


def test_environment_accepts_the_parallel_bdf_foundation(tmp_path: Path) -> None:
    # Given BDF staging points to its new bucket, role, and permission boundary.
    environment = tmp_path / "staging.json"
    environment.write_text(json.dumps(_bdf_document()), encoding="utf-8")

    # When the deployment validates and reads the foundation identity.
    validated = _run("validate", environment, "bdf", "staging")
    state_key = _run("get", environment, "bdf", "staging", "state_key")
    role_name = _run("get", environment, "bdf", "staging", "deployer_role_name")
    boundary = _run("get", environment, "bdf", "staging", "deployer_boundary_arn")

    # Then it uses only the new BDF foundation and canonical staging state key.
    assert validated.returncode == 0, validated.stderr
    assert state_key.stdout.strip() == "datachord/bdf/staging/tofu.tfstate"
    assert role_name.stdout.strip() == "bdf-datachord-deployer"
    assert boundary.stdout.strip() == ("arn:aws:iam::084828580051:policy/bdf-datachord-deployer-boundary")


def test_environment_rejects_unknown_fields_and_mismatched_foundation_names(
    tmp_path: Path,
) -> None:
    # Given environments add another source of truth or mismatch foundation names.
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
                "deployer_role_arn": ("arn:aws:iam::945365518758:role/datachord-deployer"),
            }
        ),
        encoding="utf-8",
    )
    wrong_boundary = tmp_path / "wrong-boundary.json"
    wrong_boundary.write_text(
        json.dumps(
            {
                **_document(),
                "application_role_boundary_arn": ("arn:aws:iam::945365518758:policy/another-application-role-boundary"),
            }
        ),
        encoding="utf-8",
    )

    # When each environment is validated.
    extra_field = _run("validate", unsafe, "netrias", "staging")
    mismatched_role = _run("validate", wrong_role, "netrias", "staging")
    mismatched_boundary = _run("validate", wrong_boundary, "netrias", "staging")

    # Then all stop with the exact violated boundary.
    assert extra_field.returncode == 2
    assert "unsupported environment fields: state_key" in extra_field.stderr
    assert mismatched_role.returncode == 2
    assert "role must use the /foundation/ path" in mismatched_role.stderr
    assert mismatched_boundary.returncode == 2
    assert "policy/datachord-application-role-boundary" in mismatched_boundary.stderr


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
        "deployer_role_arn": ("arn:aws-us-gov:iam::945365518758:role/foundation/datachord-deployer"),
        "application_role_boundary_arn": (
            "arn:aws-us-gov:iam::945365518758:policy/datachord-application-role-boundary"
        ),
    }
    govcloud.write_text(json.dumps(gov_document), encoding="utf-8")

    # When the environment is validated.
    unsupported = _run("validate", govcloud, "core", "staging")

    # Then deployment stops before AWS is used.
    assert unsupported.returncode == 2
    assert "does not support GovCloud" in unsupported.stderr


def test_customer_platform_uses_the_bootstrap_handoff_directly(
    tmp_path: Path,
) -> None:
    # Given bootstrap schema v2 describes only the account foundation.
    handoff = tmp_path / "foundation-handoff.json"
    handoff.write_text(json.dumps(_handoff_document()), encoding="utf-8")

    # When the customer-platform deployment reads its state and OpenTofu inputs.
    validated = _run("validate", handoff, "netrias", "staging", "customer-platform")
    state_key = _run(
        "get",
        handoff,
        "netrias",
        "staging",
        "state_key",
        "customer-platform",
    )
    variables = _run("tofu-vars", handoff, "netrias", "staging", "customer-platform")

    # Then no DNS, repository, secret, or application-role input is required.
    assert validated.returncode == 0, validated.stderr
    assert state_key.stdout.strip() == ("datachord/netrias/staging/customer-platform/tofu.tfstate")
    assert json.loads(variables.stdout) == {
        "aws_region": "us-east-2",
        "deployment_target": "netrias",
        "environment": "staging",
        "expected_account_id": "945365518758",
    }


def test_customer_platform_rejects_a_handoff_for_another_target(
    tmp_path: Path,
) -> None:
    # Given a valid handoff belongs to the netrias target.
    handoff = tmp_path / "foundation-handoff.json"
    handoff.write_text(json.dumps(_handoff_document()), encoding="utf-8")

    # When another target tries to use it.
    result = _run("validate", handoff, "customer", "staging", "customer-platform")

    # Then the boundary rejects cross-target state and role use.
    assert result.returncode == 2
    assert "handoff target must be customer" in result.stderr


def _run(action: str, environment: Path, target: str, stage: str, *arguments: str) -> subprocess.CompletedProcess[str]:
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
        "deployer_role_arn": ("arn:aws:iam::945365518758:role/foundation/datachord-deployer"),
        "application_role_boundary_arn": ("arn:aws:iam::945365518758:policy/datachord-application-role-boundary"),
        "application_role_path": "/application/",
        "domain_name": "data-chord-staging.apps.netrias.com",
        "hosted_zone_name": "apps.netrias.com",
        "application_repository_url": "https://github.com/netrias/data_chord.git",
        "github_app_secret_name": "data-chord/build/github-app",
    }


def _bdf_document() -> dict[str, object]:
    return {
        "account_id": "084828580051",
        "region": "us-east-2",
        "state_bucket_name": "bdf-datachord-state-084828580051-us-east-2",
        "deployer_role_arn": ("arn:aws:iam::084828580051:role/foundation/bdf-datachord-deployer"),
        "application_role_boundary_arn": ("arn:aws:iam::084828580051:policy/bdf-datachord-application-role-boundary"),
        "application_role_path": "/application/",
        "domain_name": "netrias-data-chord-staging.netriasbdf.cloud",
        "hosted_zone_name": "netriasbdf.cloud",
        "application_repository_url": "https://github.com/netrias/data_chord.git",
        "github_app_secret_name": "data-chord/build/github-app",
    }


def _handoff_document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "target": "netrias",
        "account_id": "945365518758",
        "partition": "aws",
        "region": "us-east-2",
        "state_bucket_name": "netrias-datachord-state-945365518758-us-east-2",
        "protected_state_bucket_name": None,
        "state_key_prefix": "datachord/netrias/",
        "deployer_role_arn": ("arn:aws:iam::945365518758:role/foundation/datachord-deployer"),
        "deployer_boundary_arn": ("arn:aws:iam::945365518758:policy/datachord-deployer-boundary"),
        "application_role_boundary_arn": ("arn:aws:iam::945365518758:policy/datachord-application-role-boundary"),
        "application_role_path": "/application/",
        "assume_role_policy_statement": {
            "Effect": "Allow",
            "Action": "sts:AssumeRole",
            "Resource": ("arn:aws:iam::945365518758:role/foundation/datachord-deployer"),
        },
    }
