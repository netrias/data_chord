from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra" / "scripts" / "deployment_receipt.py"
COMMIT = "a" * 40


def test_receipt_binds_the_preview_to_code_configuration_and_state(
    tmp_path: Path,
) -> None:
    # Given one validated environment, state identity, and safe deployment preview.
    environment = _environment(tmp_path)
    state = _state(tmp_path, serial=7)
    plan = _plan(tmp_path, {"aws_ecr_repository.app": ["create"]})
    receipt = tmp_path / "receipt.json"
    assert not receipt.exists()

    # When the plan writes its receipt and deploy validates the same inputs.
    created = _run(
        "create",
        "--receipt",
        receipt,
        "--environment",
        environment,
        "--target",
        "netrias",
        "--stage",
        "staging",
        "--deployment-root",
        "full",
        "--commit",
        COMMIT,
        "--state",
        state,
        "--plan-json",
        plan,
    )
    validated = _validate(receipt, environment, state)

    # Then the complete bounded forecast is valid and stored atomically.
    assert created.returncode == 0, created.stderr
    assert validated.returncode == 0, validated.stderr
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["status"] == "planned"
    assert document["state"] == {"kind": "present", "lineage": "lineage-1", "serial": 7}
    assert document["forecast"] == [{"address": "aws_ecr_repository.app", "actions": ["create"]}]
    assert set(document) == {
        "account_id",
        "commit",
        "config_digest",
        "deployment_root",
        "forecast",
        "partition",
        "region",
        "repository_url",
        "schema_version",
        "stage",
        "state",
        "state_bucket_name",
        "state_key",
        "status",
        "target",
    }
    assert document["deployment_root"] == "full"


def test_receipt_rejects_changed_state_before_deploy(tmp_path: Path) -> None:
    # Given a plan receipt was created at state serial seven.
    environment = _environment(tmp_path)
    state = _state(tmp_path, serial=7)
    receipt = tmp_path / "receipt.json"
    _create(receipt, environment, state, _plan(tmp_path, {}))
    _state(tmp_path, serial=8)

    # When deploy validates the receipt against the current state.
    result = _validate(receipt, environment, state)

    # Then it stops before any deployment phase can start.
    assert result.returncode == 2
    assert "no longer matches state" in result.stderr


def test_receipt_treats_the_new_backend_state_as_absent(tmp_path: Path) -> None:
    # Given OpenTofu returns its empty state document for a new backend.
    environment = _environment(tmp_path)
    state = tmp_path / "empty-state.json"
    state.write_text(
        json.dumps(
            {
                "version": 4,
                "terraform_version": "1.10.3",
                "serial": 0,
                "lineage": "",
                "outputs": {},
                "resources": [],
                "check_results": None,
            }
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"

    # When plan records the current state identity.
    _create(receipt, environment, state, _plan(tmp_path, {}))

    # Then the valid fresh backend is recorded as absent state.
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["state"] == {"kind": "absent"}


def test_in_progress_receipt_rechecks_state_before_first_apply(tmp_path: Path) -> None:
    # Given deploy validated its receipt and then marked it in progress.
    environment = _environment(tmp_path)
    state = _state(tmp_path, serial=7)
    receipt = tmp_path / "receipt.json"
    _create(receipt, environment, state, _plan(tmp_path, {}))
    status = _run(
        "status",
        "--receipt",
        receipt,
        "--from-status",
        "planned",
        "--to-status",
        "in_progress",
    )
    assert status.returncode == 0, status.stderr

    # When another actor changes the remote state before the first apply.
    _state(tmp_path, serial=8)
    result = _validate(receipt, environment, state, status="in_progress")

    # Then deploy rejects the changed state before it can apply its saved plan.
    assert result.returncode == 2
    assert "no longer matches state" in result.stderr


def test_internal_plans_stay_inside_the_forecast_and_phase(tmp_path: Path) -> None:
    # Given a deployment is in progress with one approved prerequisite resource.
    environment = _environment(tmp_path)
    receipt = tmp_path / "receipt.json"
    state = _state(tmp_path, serial=1)
    _create(
        receipt,
        environment,
        state,
        _plan(tmp_path, {"aws_ecr_repository.app": ["create"]}),
    )
    _run(
        "status",
        "--receipt",
        receipt,
        "--from-status",
        "planned",
        "--to-status",
        "in_progress",
    )
    approved = _plan(tmp_path, {"aws_ecr_repository.app": ["create"]}, name="approved.json")
    extra = _plan(tmp_path, {"aws_vpc.app": ["create"]}, name="extra.json")

    # When the prerequisite phase checks the approved and extra plans.
    accepted = _check(receipt, approved, "prerequisite")
    rejected = _check(receipt, extra, "prerequisite")

    # Then only the forecasted prerequisite resource can proceed.
    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode == 2
    assert "unexpected resource: aws_vpc.app" in rejected.stderr


def test_receipt_rejects_destructive_preview_and_reuse(tmp_path: Path) -> None:
    # Given one preview deletes durable application state and one safe receipt completes.
    environment = _environment(tmp_path)
    state = _state(tmp_path, serial=1)
    destructive = _plan(tmp_path, {"aws_dynamodb_table.reference_data": ["delete", "create"]})
    receipt = tmp_path / "receipt.json"

    # When plan records the destructive forecast and deploy tries to reuse a used receipt.
    blocked = _run(
        "create",
        "--receipt",
        receipt,
        "--environment",
        environment,
        "--target",
        "netrias",
        "--stage",
        "staging",
        "--deployment-root",
        "full",
        "--commit",
        COMMIT,
        "--state",
        state,
        "--plan-json",
        destructive,
    )
    _create(receipt, environment, state, _plan(tmp_path, {}, name="safe.json"))
    _run(
        "status",
        "--receipt",
        receipt,
        "--from-status",
        "planned",
        "--to-status",
        "complete",
    )
    reused = _validate(receipt, environment, state)

    # Then destructive work and a second deploy both require a new safe plan.
    assert blocked.returncode == 2
    assert "destructive deploys are not supported" in blocked.stderr
    assert reused.returncode == 2
    assert "no longer matches status" in reused.stderr


def test_customer_platform_receipt_uses_its_own_state_identity(
    tmp_path: Path,
) -> None:
    # Given one bootstrap handoff and an empty customer-platform state.
    handoff = _handoff(tmp_path)
    receipt = tmp_path / "customer-platform-receipt.json"
    plan = _plan(
        tmp_path,
        {
            "module.data_plane.aws_s3_bucket.workflow": ["create"],
            "module.data_plane.aws_s3_bucket_public_access_block.workflow": ["create"],
            "module.data_plane.aws_dynamodb_table.reference_data": ["create"],
            "module.data_plane.aws_dynamodb_table.harmonization_cache": ["create"],
            "module.data_plane.aws_dynamodb_table.cde_recommendation_cache": ["create"],
        },
    )

    # When the customer-platform plan creates its bounded receipt.
    result = _run(
        "create",
        "--receipt",
        receipt,
        "--environment",
        handoff,
        "--target",
        "netrias",
        "--stage",
        "staging",
        "--deployment-root",
        "customer-platform",
        "--commit",
        COMMIT,
        "--state",
        "-",
        "--plan-json",
        plan,
    )

    # Then the receipt cannot be confused with the full deployment state.
    assert result.returncode == 0, result.stderr
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["deployment_root"] == "customer-platform"
    assert document["state_key"] == ("datachord/netrias/staging/customer-platform/tofu.tfstate")
    assert document["repository_url"] is None


def _create(receipt: Path, environment: Path, state: Path, plan: Path) -> None:
    result = _run(
        "create",
        "--receipt",
        receipt,
        "--environment",
        environment,
        "--target",
        "netrias",
        "--stage",
        "staging",
        "--deployment-root",
        "full",
        "--commit",
        COMMIT,
        "--state",
        state,
        "--plan-json",
        plan,
    )
    assert result.returncode == 0, result.stderr


def _validate(
    receipt: Path,
    environment: Path,
    state: Path,
    *,
    status: str = "planned",
) -> subprocess.CompletedProcess[str]:
    return _run(
        "validate",
        "--receipt",
        receipt,
        "--environment",
        environment,
        "--target",
        "netrias",
        "--stage",
        "staging",
        "--deployment-root",
        "full",
        "--commit",
        COMMIT,
        "--state",
        state,
        "--expected-status",
        status,
    )


def _check(receipt: Path, plan: Path, phase: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "check-plan",
        "--receipt",
        receipt,
        "--plan-json",
        plan,
        "--phase",
        phase,
    )


def _run(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), *(str(argument) for argument in arguments)],
        check=False,
        capture_output=True,
        text=True,
    )


def _environment(tmp_path: Path) -> Path:
    path = tmp_path / "environment.json"
    path.write_text(
        json.dumps(
            {
                "account_id": "945365518758",
                "region": "us-east-2",
                "state_bucket_name": "netrias-datachord-state-945365518758-us-east-2",
                "deployer_role_arn": ("arn:aws:iam::945365518758:role/foundation/datachord-deployer"),
                "application_role_boundary_arn": (
                    "arn:aws:iam::945365518758:policy/datachord-application-role-boundary"
                ),
                "application_role_path": "/application/",
                "domain_name": "data-chord-staging.apps.netrias.com",
                "hosted_zone_name": "apps.netrias.com",
                "application_repository_url": "https://github.com/netrias/data_chord.git",
                "github_app_secret_name": "data-chord/build/github-app",
            }
        ),
        encoding="utf-8",
    )
    return path


def _handoff(tmp_path: Path) -> Path:
    path = tmp_path / "foundation-handoff.json"
    role_arn = "arn:aws:iam::945365518758:role/foundation/datachord-deployer"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "target": "netrias",
                "account_id": "945365518758",
                "partition": "aws",
                "region": "us-east-2",
                "state_bucket_name": ("netrias-datachord-state-945365518758-us-east-2"),
                "protected_state_bucket_name": None,
                "state_key_prefix": "datachord/netrias/",
                "deployer_role_arn": role_arn,
                "deployer_boundary_arn": ("arn:aws:iam::945365518758:policy/datachord-deployer-boundary"),
                "application_role_boundary_arn": (
                    "arn:aws:iam::945365518758:policy/datachord-application-role-boundary"
                ),
                "application_role_path": "/application/",
                "assume_role_policy_statement": {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Resource": role_arn,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _state(tmp_path: Path, serial: int) -> Path:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"lineage": "lineage-1", "serial": serial}), encoding="utf-8")
    return path


def _plan(tmp_path: Path, changes: dict[str, list[str]], name: str = "plan.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "resource_changes": [
                    {"address": address, "change": {"actions": actions}} for address, actions in changes.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    return path
