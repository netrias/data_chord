"""Behavior tests for the direct local demo launcher."""

from __future__ import annotations

import os
import signal
import socket
import subprocess  # nosec B404 - the feature under test owns a local child process
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).parents[1]


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _runner_environment(runtime_parent: Path, port: int) -> dict[str, str]:
    return {
        **os.environ,
        "DATA_CHORD_DEMO_PORT": str(port),
        "TMPDIR": str(runtime_parent),
    }


def _wait_for_stage_one(process: subprocess.Popen[str], port: int) -> str:
    url = f"http://127.0.0.1:{port}/stage-1"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output, _stderr = process.communicate()
            raise AssertionError(f"Local demo exited before it was ready:\n{output}")
        try:
            with urlopen(url, timeout=0.5) as response:  # noqa: S310 - fixed local test URL
                return response.read().decode("utf-8")
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError("Local demo did not become ready")


def _start_runner(runtime_parent: Path, port: int) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and module
        [sys.executable, "-m", "scripts.run_demo_local", "--no-browser"],
        cwd=PROJECT_ROOT,
        env=_runner_environment(runtime_parent, port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _stop_runner(process: subprocess.Popen[str], stop_signal: signal.Signals) -> str:
    process.send_signal(stop_signal)
    output, _stderr = process.communicate(timeout=15)
    return output


def _ensure_runner_stopped(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        # The runner allows two ten-second child shutdown phases before force-killing.
        process.wait(timeout=25)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_local_demo_starts_packaged_file_and_cleans_up(tmp_path: Path) -> None:
    # Given a free port and an empty parent for the runner-owned temporary data.
    port = _available_port()
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()
    assert list(runtime_parent.iterdir()) == []

    # When the direct local runner starts and is then interrupted.
    process = _start_runner(runtime_parent, port)
    try:
        page = _wait_for_stage_one(process, port)
        output = _stop_runner(process, signal.SIGINT)
    finally:
        _ensure_runner_stopped(process)

    # Then Stage 1 shows the packaged file and shutdown removes its runtime.
    assert process.returncode == 0, output
    assert "sample.csv" in page
    assert "Demo file locked" in page
    assert list(runtime_parent.iterdir()) == []

    # And the same port can restart immediately, then survive a closed terminal cleanly.
    restarted_process = _start_runner(runtime_parent, port)
    try:
        restarted_page = _wait_for_stage_one(restarted_process, port)
        restarted_output = _stop_runner(restarted_process, signal.SIGHUP)
    finally:
        _ensure_runner_stopped(restarted_process)
    assert restarted_process.returncode == 0, restarted_output
    assert "sample.csv" in restarted_page
    assert list(runtime_parent.iterdir()) == []


def test_local_demo_rejects_an_occupied_port(tmp_path: Path) -> None:
    # Given another process already owns the selected port.
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])

        # When the direct local runner starts on that port.
        result = subprocess.run(  # noqa: S603 - fixed interpreter and module
            [sys.executable, "-m", "scripts.run_demo_local", "--no-browser"],
            cwd=PROJECT_ROOT,
            env=_runner_environment(runtime_parent, port),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    # Then it fails immediately with an actionable error and creates no runtime.
    assert result.returncode == 2
    assert f"Port {port} is already in use" in result.stderr
    assert list(runtime_parent.iterdir()) == []
