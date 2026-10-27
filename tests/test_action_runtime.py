"""Prove runtime selection cannot attest an opted-out or unidentified engine."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/check-action-runtime.sh"


@pytest.mark.parametrize("opt_out", ["true", "TRUE", "True", "1", "$true", "$TRUE", "unknown"])
def test_insecure_action_runtime_selection_is_rejected(opt_out: str) -> None:
    # Given the runner can interpret an opt-out as true, or its meaning is unknown.
    environment = os.environ.copy()
    environment["FORCE_JAVASCRIPT_ACTIONS_TO_NODE24"] = "true"
    environment["ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION"] = opt_out

    # When the real audit script checks the workflow runtime selection.
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=environment, capture_output=True, text=True, check=False
    )

    # Then it refuses to attest a different binary from the one actions can select.
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "An insecure JavaScript action-runtime opt-out is not permitted.\n"


@pytest.mark.parametrize("opt_out", ["", "false", "0", "$false"])
def test_unidentified_worker_is_not_attested(opt_out: str) -> None:
    # Given safe runtime flags but this Python test process is not Runner.Worker.
    environment = os.environ.copy()
    environment["FORCE_JAVASCRIPT_ACTIONS_TO_NODE24"] = "true"
    environment["ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION"] = opt_out

    # When the real script tries to identify its runner-owned binary.
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=environment, capture_output=True, text=True, check=False
    )

    # Then safe flags alone cannot produce a successful runtime report.
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Cannot identify the GitHub runner worker" in result.stderr
