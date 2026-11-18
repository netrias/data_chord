"""Exercise the same Ruff policy used by local hooks and hosted CI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

_CONFIG = Path(__file__).resolve().parents[1] / "ruff-complexity.toml"


@pytest.mark.parametrize("suppression", ["", "  # noqa: C901, PLR0912, PLR0915"])
def test_gate_rejects_complexity_and_branches_even_with_suppressions(tmp_path: Path, suppression: str) -> None:
    # Given a function with nine independent branches, optionally hiding them with noqa.
    source = tmp_path / "branches.py"
    source.write_text(f"def choose(x):{suppression}\n" + "    if x:\n        x -= 1\n" * 9 + "    return x\n")

    # When the actual application complexity check examines that function.
    result = _check(source)

    # Then the complexity and branch violations cannot be suppressed.
    assert result.returncode == 1, result.stdout + result.stderr
    assert {row["code"] for row in json.loads(result.stdout)} == {"C901", "PLR0912"}


@pytest.mark.parametrize("statements,exit_code", [(20, 0), (21, 1)])
def test_gate_enforces_the_twenty_statement_boundary(tmp_path: Path, statements: int, exit_code: int) -> None:
    # Given straight-line functions at and immediately above Ruff's statement limit.
    source = tmp_path / "statements.py"
    source.write_text("def build():  # noqa: PLR0915\n" + "    x = 1\n" * statements + "    return x\n")

    # When the actual gate examines the function, without exempting its noqa.
    result = _check(source)

    # Then twenty statements pass and twenty-one fail on the statement rule.
    assert result.returncode == exit_code, result.stdout + result.stderr
    expected = set() if exit_code == 0 else {"PLR0915"}
    assert {row["code"] for row in json.loads(result.stdout)} == expected


def test_gate_accepts_complexity_at_the_limit(tmp_path: Path) -> None:
    # Given seven branches plus one entry point, giving cyclomatic complexity eight.
    source = tmp_path / "allowed.py"
    source.write_text("def choose(x):\n" + "    if x:\n        x -= 1\n" * 7 + "    return x\n")

    # When the gate evaluates the boundary function.
    result = _check(source)

    # Then it passes without an exception or suppression.
    assert result.returncode == 0, result.stdout + result.stderr


def _check(source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(_CONFIG),
            "--ignore-noqa",
            "--output-format",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
