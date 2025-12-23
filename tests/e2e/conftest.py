"""Playwright fixtures for end-to-end browser tests."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
PROJECT_ROOT = TESTS_DIR.parent

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


@pytest.fixture(scope="session")
def server() -> Generator[str]:
    """why: start the FastAPI server for E2E tests."""
    env = os.environ.copy()
    env["UPLOAD_STORAGE_PATH"] = str(PROJECT_ROOT / "test_uploads")

    process = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "backend.app.main:create_app",
            "--factory",
            "--host",
            SERVER_HOST,
            "--port",
            str(SERVER_PORT),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    _wait_for_server(BASE_URL, timeout=30)

    yield BASE_URL

    process.terminate()
    process.wait(timeout=5)


def _wait_for_server(url: str, timeout: int = 30) -> None:
    """why: poll until server responds or timeout."""
    import httpx

    start = time.time()
    while time.time() - start < timeout:
        try:
            response = httpx.get(f"{url}/stage-1", timeout=1)
            if response.status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Server did not start within {timeout} seconds")


@pytest.fixture
def sample_csv_path() -> Path:
    """why: provide path to test CSV fixture."""
    return FIXTURES_DIR / "sample.csv"
