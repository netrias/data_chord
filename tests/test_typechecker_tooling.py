"""Exercise runtime integrity and Python interpreter selection at the CLI boundary."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


def _command(directory: Path, name: str, body: str) -> None:
    command = directory / name
    command.write_text(f"#!/bin/sh\nset -eu\n{body}\n")
    command.chmod(0o755)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "config").mkdir()
    for name in ("install-node-runtime.sh", "typecheck-python.mjs"):
        shutil.copyfile(PROJECT_ROOT / "scripts" / name, root / "scripts" / name)
    return root


def _environment(tmp_path: Path) -> dict[str, str]:
    commands = tmp_path / "commands"
    commands.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}", "TMPDIR": str(runtime)}


def _archive(tmp_path: Path) -> Path:
    archive = tmp_path / "node.tar.gz"
    payload = b"#!/bin/sh\nprintf 'fixture node\\n'\n"
    with tarfile.open(archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("node-fixture/bin/node")
        entry.size = len(payload)
        entry.mode = 0o755
        bundle.addfile(entry, io.BytesIO(payload))
    return archive


def _mock_download(tmp_path: Path, env: dict[str, str], archive: Path) -> None:
    env["MOCK_ARCHIVE"] = str(archive)
    env["MOCK_DOWNLOAD_LOG"] = str(tmp_path / "download.log")
    commands = tmp_path / "commands"
    _command(commands, "uname", 'case "$1" in -s) echo Linux;; -m) echo x86_64;; esac')
    _command(
        commands,
        "curl",
        'printf "%s\\n" "$@" > "$MOCK_DOWNLOAD_LOG"\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --output ]; then cp "$MOCK_ARCHIVE" "$2"; exit 0; fi\n'
        "  shift\n"
        "done\nexit 2",
    )


def _run_installer(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts/install-node-runtime.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_verified_node_archive_is_installed(tmp_path: Path) -> None:
    # Given an approved archive hash and an empty installation directory.
    root = _workspace(tmp_path)
    env = _environment(tmp_path)
    archive = _archive(tmp_path)
    _mock_download(tmp_path, env, archive)
    (root / ".nvmrc").write_text("99.0.0\n")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (root / "config/node-runtime-sha256.txt").write_text(f"{checksum}  node-v99.0.0-linux-x64.tar.gz\n")
    assert list((tmp_path / "runtime").iterdir()) == []

    # When the installer downloads the archive and verifies it before extraction.
    result = _run_installer(root, env)

    # Then it returns an executable installation from the approved exact release.
    assert result.returncode == 0, result.stderr
    installation = Path(result.stdout.strip())
    assert (installation / "bin/node").read_bytes() == b"#!/bin/sh\nprintf 'fixture node\\n'\n"
    assert os.access(installation / "bin/node", os.X_OK)
    assert "https://nodejs.org/dist/v99.0.0/node-v99.0.0-linux-x64.tar.gz" in (tmp_path / "download.log").read_text()


def test_modified_archive_is_rejected_before_extraction(tmp_path: Path) -> None:
    # Given a download whose bytes do not match the reviewed release checksum.
    root = _workspace(tmp_path)
    env = _environment(tmp_path)
    archive = _archive(tmp_path)
    _mock_download(tmp_path, env, archive)
    (root / ".nvmrc").write_text("99.0.0\n")
    (root / "config/node-runtime-sha256.txt").write_text(f"{'0' * 64}  node-v99.0.0-linux-x64.tar.gz\n")
    marker = tmp_path / "extracted"
    env["MOCK_EXTRACTION_MARKER"] = str(marker)
    _command(tmp_path / "commands", "tar", 'touch "$MOCK_EXTRACTION_MARKER"')
    assert not marker.exists()

    # When the altered archive reaches the integrity check.
    result = _run_installer(root, env)

    # Then no downloaded content is extracted or executed.
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not marker.exists()


def test_unreviewed_runtime_pin_does_not_download(tmp_path: Path) -> None:
    # Given a version pin without an approved archive checksum.
    root = _workspace(tmp_path)
    env = _environment(tmp_path)
    archive = _archive(tmp_path)
    _mock_download(tmp_path, env, archive)
    (root / ".nvmrc").write_text("99.0.1\n")
    (root / "config/node-runtime-sha256.txt").write_text(f"{'0' * 64}  node-v99.0.0-linux-x64.tar.gz\n")
    assert not (tmp_path / "download.log").exists()

    # When an unreviewed runtime version is requested.
    result = _run_installer(root, env)

    # Then the installer fails before any network download.
    assert result.returncode != 0
    assert "reviewed checksum" in result.stderr
    assert not (tmp_path / "download.log").exists()


def _checker(root: Path, tmp_path: Path, env: dict[str, str]) -> None:
    checker = root / "node_modules/basedpyright/index.js"
    checker.parent.mkdir(parents=True)
    checker.write_text(
        "require('node:fs').writeFileSync(process.env.MOCK_CHECKER_LOG, "
        "JSON.stringify({args:process.argv.slice(2),node:process.execPath}));\n"
    )
    env["MOCK_CHECKER_LOG"] = str(tmp_path / "checker.json")


def _run_checker(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(root / "scripts/typecheck-python.mjs"), "--level", "error", "src/"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _current_node_version() -> str:
    return subprocess.check_output(["node", "-p", "process.versions.node"], text=True).strip()


def test_checker_uses_the_discovered_python_and_same_node(tmp_path: Path) -> None:
    # Given a custom Python environment and a checker that has not been launched.
    root = _workspace(tmp_path)
    env = _environment(tmp_path)
    _checker(root, tmp_path, env)
    (root / ".nvmrc").write_text(f"{_current_node_version()}\n")
    env["MOCK_PYTHON"] = sys.executable
    _command(tmp_path / "commands", "uv", 'printf "%s\\n" "$MOCK_PYTHON"')
    assert not (tmp_path / "checker.json").exists()

    # When interpreter discovery returns the selected environment's absolute path.
    result = _run_checker(root, env)

    # Then the checker gets that interpreter, the forwarded scope, and the validated Node executable.
    assert result.returncode == 0, result.stderr
    invocation = json.loads((tmp_path / "checker.json").read_text())
    assert invocation["args"] == ["--pythonpath", sys.executable, "--level", "error", "src/"]
    assert Path(invocation["node"]).resolve() == Path(shutil.which("node") or "").resolve()


def test_python_discovery_failure_does_not_run_checker(tmp_path: Path) -> None:
    # Given uv cannot select the requested project environment.
    root = _workspace(tmp_path)
    env = _environment(tmp_path)
    _checker(root, tmp_path, env)
    (root / ".nvmrc").write_text(f"{_current_node_version()}\n")
    _command(tmp_path / "commands", "uv", 'echo "environment unavailable" >&2; exit 41')
    assert not (tmp_path / "checker.json").exists()

    # When Python discovery fails.
    result = _run_checker(root, env)

    # Then the command fails with the discovery status and never falls back to another Python.
    assert result.returncode == 41, result.stderr
    assert "environment unavailable" in result.stderr
    assert not (tmp_path / "checker.json").exists()


def test_wrong_node_is_rejected_before_interpreter_discovery(tmp_path: Path) -> None:
    # Given the installed Node does not match the project's selected runtime.
    root = _workspace(tmp_path)
    env = _environment(tmp_path)
    _checker(root, tmp_path, env)
    (root / ".nvmrc").write_text("99.0.0\n")
    marker = tmp_path / "discovered"
    env["MOCK_DISCOVERY_MARKER"] = str(marker)
    _command(tmp_path / "commands", "uv", 'touch "$MOCK_DISCOVERY_MARKER"')
    assert not marker.exists()

    # When typechecking is requested under a different Node, even after dependencies exist.
    result = _run_checker(root, env)

    # Then it fails before either discovery or checker execution.
    assert result.returncode != 0
    assert "Node 99.0.0" in result.stderr
    assert not marker.exists()
    assert not (tmp_path / "checker.json").exists()
