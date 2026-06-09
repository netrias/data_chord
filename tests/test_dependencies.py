"""Feature tests for app service wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from netrias_client import Environment

import src.app.dependencies as dependencies


def test_upload_storage_uses_configured_scratch_dir(monkeypatch, tmp_path: Path) -> None:
    # Given: the hosted runtime points upload scratch at a writable directory
    scratch_dir = tmp_path / "scratch"
    monkeypatch.setenv("DATA_CHORD_UPLOAD_DIR", str(scratch_dir))
    monkeypatch.setattr(dependencies, "_storage", None)
    assert not (scratch_dir / "files").exists()

    # When: upload storage is initialized through normal app wiring
    storage = dependencies.get_upload_storage()

    # Then: scratch directories are created under the configured location
    assert storage is not None
    assert (scratch_dir / "files").is_dir()
    assert (scratch_dir / "meta").is_dir()


def test_netrias_client_uses_configured_timeout(monkeypatch) -> None:
    # Given: hosted runtime configures a longer wait for large harmonization jobs
    monkeypatch.setenv("NETRIAS_API_KEY", "test-key")
    monkeypatch.setenv("DATA_CHORD_NETRIAS_TIMEOUT_SECONDS", "3600")
    monkeypatch.setattr(dependencies, "_netrias_client", None)
    monkeypatch.setattr(dependencies, "_netrias_client_initialized", False)

    # When: the shared Netrias client is initialized
    with patch("src.app.dependencies.NetriasClient") as client_class:
        client = dependencies.get_netrias_client()

    # Then: the SDK receives the configured timeout
    assert client is client_class.return_value
    client_class.assert_called_once_with(api_key="test-key", environment=Environment.STAGING)
    client_class.return_value.configure.assert_called_once_with(timeout=3600.0)


def test_netrias_client_uses_configured_environment(monkeypatch) -> None:
    # Given: prod runtime tells the app to use prod Netrias services
    monkeypatch.setenv("NETRIAS_API_KEY", "test-key")
    monkeypatch.setenv("DATA_CHORD_NETRIAS_ENVIRONMENT", "prod")
    monkeypatch.setattr(dependencies, "_netrias_client", None)
    monkeypatch.setattr(dependencies, "_netrias_client_initialized", False)

    # When: the shared Netrias client is initialized
    with patch("src.app.dependencies.NetriasClient") as client_class:
        client = dependencies.get_netrias_client()

    # Then: the SDK is wired to prod endpoints
    assert client is client_class.return_value
    client_class.assert_called_once_with(api_key="test-key", environment=Environment.PROD)
