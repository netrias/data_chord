#!/usr/bin/env python3
# ruff: noqa: S404, S603
"""Run the packaged demo from the current working tree."""

from __future__ import annotations

import argparse
import errno
import os
import signal
import socket
import subprocess  # nosec B404
import sys
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

_DEFAULT_PORT = 8000
_STARTUP_TIMEOUT_SECONDS = 30.0
_SHUTDOWN_TIMEOUT_SECONDS = 10.0


class LocalDemoError(RuntimeError):
    """The local demo could not start or stop safely."""


class _StopRequested(Exception):
    """The user asked the local demo runner to stop."""


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Data Chord demo without Docker")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open Stage 1 automatically",
    )
    return parser.parse_args(arguments)


def _demo_port() -> int:
    raw_port = os.getenv("DATA_CHORD_DEMO_PORT", str(_DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise LocalDemoError("DATA_CHORD_DEMO_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise LocalDemoError("DATA_CHORD_DEMO_PORT must be between 1 and 65535")
    return port


def _require_available_port(port: int) -> None:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise LocalDemoError(f"Port {port} is already in use") from exc
            raise LocalDemoError(f"Could not check port {port}: {exc.strerror}") from exc


def _server_environment(runtime_root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATA_CHORD_DATA_DIR": str(runtime_root / "data"),
        "DATA_CHORD_UPLOAD_DIR": str(runtime_root / "uploads"),
        "DATA_CHORD_IDENTITY_SOURCE": "shared",
    }


def _wait_until_ready(process: subprocess.Popen[bytes], url: str) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise LocalDemoError(f"Local demo exited before it was ready with status {exit_code}")
        try:
            # The URL is constructed from a fixed loopback host and validated port.
            with urlopen(url, timeout=0.5):  # nosec B310
                return
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise LocalDemoError(f"Local demo did not become ready at {url}")


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise LocalDemoError("Local demo required a forced stop") from exc


def _request_stop(_signum: int, _frame: object) -> None:
    raise _StopRequested


def _run_demo(port: int, *, open_browser: bool) -> int:
    url = f"http://127.0.0.1:{port}/stage-1"
    with TemporaryDirectory(prefix="data-chord-demo-") as temporary_directory:
        runtime_root = Path(temporary_directory)
        command = [
            sys.executable,
            "-m",
            "scripts.demo",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--reload",
        ]
        print("Starting the Data Chord demo from the current working tree.", flush=True)
        print(f"Temporary demo data: {runtime_root}", flush=True)
        # The child command uses this Python interpreter and never opens a shell.
        process = subprocess.Popen(  # nosec B603
            command,
            env=_server_environment(runtime_root),
            start_new_session=True,
        )
        try:
            _wait_until_ready(process, url)
            print(f"Data Chord local demo is ready at {url}", flush=True)
            if open_browser:
                webbrowser.open(url)
            return process.wait()
        except _StopRequested:
            _stop_process_group(process)
            return 0
        except BaseException:
            _stop_process_group(process)
            raise


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parse_args(arguments)
    try:
        port = _demo_port()
        _require_available_port(port)
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGHUP, _request_stop)
        return _run_demo(port, open_browser=not args.no_browser)
    except LocalDemoError as exc:
        print(f"Data Chord local demo could not start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
