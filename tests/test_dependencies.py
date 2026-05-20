"""Feature tests for app service wiring."""

from __future__ import annotations

from pathlib import Path

from src.domain import dependencies


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
