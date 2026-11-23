"""Check the customer root's mock-provider plan using the deployment receipt CLI."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

_RECEIPT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deployment_receipt.py"
_LEGACY_VERSIONING = "module.data_plane.aws_s3_bucket_versioning.workflow"


def _create_receipt(directory: Path, handoff: Path, plan: object) -> subprocess.CompletedProcess[str]:
    plan_path = directory / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(_RECEIPT_SCRIPT),
            "create",
            "--receipt",
            str(directory / "receipt.json"),
            "--environment",
            str(handoff),
            "--target",
            "example",
            "--stage",
            "staging",
            "--deployment-root",
            "customer-platform",
            "--commit",
            "a" * 40,
            "--state",
            "-",
            "--plan-json",
            str(plan_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


# Given a successful plan-only test and a matching synthetic foundation handoff.
messages = [json.loads(line) for line in sys.stdin]
assert any(message.get("test_summary", {}).get("status") == "pass" for message in messages)
(plan,) = [message["test_plan"] for message in messages if message["type"] == "test_plan"]
with TemporaryDirectory() as scratch:
    directory = Path(scratch)
    handoff = directory / "handoff.json"
    deployer = "arn:aws:iam::123456789012:role/foundation/example-deployer"
    handoff.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "target": "example",
                "account_id": "123456789012",
                "partition": "aws",
                "region": "us-east-2",
                "state_bucket_name": "example-deployment-state",
                "protected_state_bucket_name": None,
                "state_key_prefix": "datachord/example/",
                "deployer_role_arn": deployer,
                "deployer_boundary_arn": "arn:aws:iam::123456789012:policy/example-deployer-boundary",
                "application_role_boundary_arn": "arn:aws:iam::123456789012:policy/example-application-role-boundary",
                "application_role_path": "/application/",
                "assume_role_policy_statement": {"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": deployer},
            }
        ),
        encoding="utf-8",
    )

    # When the real receipt command checks the plan and possible versioning actions.
    plan_result = _create_receipt(directory, handoff, plan)
    versioning_results = {
        action: _create_receipt(
            directory,
            handoff,
            {
                "resource_changes": [{"address": _LEGACY_VERSIONING, "change": {"actions": [action]}}],
            },
        )
        for action in ("forget", "create", "update", "delete")
    }

    # Then it admits the five creates and state-only forget and rejects mutation.
    assert plan_result.returncode == 0, plan_result.stderr
    managed = [resource for resource in plan["resource_changes"] if resource["mode"] == "managed"]
    assert len(managed) == 5 and all(resource["change"]["actions"] == ["create"] for resource in managed)
    assert versioning_results["forget"].returncode == 0, versioning_results["forget"].stderr
    for action in ("create", "update", "delete"):
        outcome = versioning_results[action]
        assert outcome.returncode == 2 and _LEGACY_VERSIONING in outcome.stderr, outcome.stderr

print("Customer plan accepted five creates and a state-only versioning handoff.")
