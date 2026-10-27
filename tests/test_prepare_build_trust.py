"""Verify the trust-store transition and its fail-closed recovery behavior."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import certifi
import pytest

ROOT = Path(__file__).parents[1]
PREPARE = ROOT / "scripts/prepare-build-trust.py"
CHECKER = ROOT / "scripts/check-build-host-trust.py"


@dataclass(frozen=True)
class _PreparationFiles:
    original_store: Path
    store: Path
    archive: Path
    pin: Path
    bundle: bytes


def _write_archive(archive: Path, pin: Path, bundle: bytes) -> None:
    with zipfile.ZipFile(archive, "w") as wheel:
        wheel.writestr("certifi/cacert.pem", bundle)
    pin.write_text(json.dumps({
        "url": "https://files.pythonhosted.org/packages/test/certifi.whl",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }))


@pytest.fixture
def preparation(tmp_path: Path) -> _PreparationFiles:
    store = tmp_path / "certs"
    store.mkdir()
    (store / "old-root.pem").write_bytes(b"previous trust data")
    archive = tmp_path / "certifi.whl"
    pin = tmp_path / "pin.json"
    bundle = Path(certifi.where()).read_bytes()
    _write_archive(archive, pin, bundle)
    return _PreparationFiles(store, tmp_path / "approved-certs", archive, pin, bundle)


def _prepare(files: _PreparationFiles, checker: Path = CHECKER) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(PREPARE), "--pin", str(files.pin), "--checker", str(checker),
         "--archive", str(files.archive), "--store", str(files.store)],
        capture_output=True, text=True, check=False,
    )


def _assert_original_store(files: _PreparationFiles) -> None:
    assert set(p.name for p in files.original_store.iterdir()) == {"old-root.pem"}
    assert (files.original_store / "old-root.pem").read_bytes() == b"previous trust data"


def test_verified_data_prepares_a_separate_effective_store(preparation: _PreparationFiles) -> None:
    # Given a pinned public artifact and an unchanged image-owned trust store.
    files = preparation
    _assert_original_store(files)
    assert not files.store.exists()

    # When the real preparer and certificate audit prepare a new store.
    result = _prepare(files)

    # Then only the verified bundle is available for selection; original trust is untouched.
    assert result.returncode == 0, result.stderr
    assert set(p.name for p in files.store.iterdir()) == {"ca-certificates.crt"}
    assert (files.store / "ca-certificates.crt").read_bytes() == files.bundle
    reports = [json.loads(line) for line in result.stdout.splitlines()]
    assert reports[-1] == {"bundle_sha256": hashlib.sha256(files.bundle).hexdigest(), "store": str(files.store)}
    _assert_original_store(files)


def test_wrong_artifact_hash_cannot_change_trust(preparation: _PreparationFiles) -> None:
    # Given downloaded bytes do not match the reviewed artifact hash.
    files = preparation
    files.archive.write_bytes(files.archive.read_bytes() + b"changed")

    # When the real preparer checks the artifact before opening it.
    result = _prepare(files)

    # Then the original store remains exact and no replacement is reported.
    assert result.returncode == 1
    assert "SHA-256 mismatch" in result.stderr
    assert result.stdout == ""
    _assert_original_store(files)
    assert not files.store.exists()


@pytest.mark.parametrize("name", ["emsign-g3.pem", "aaa-services.pem"])
def test_pinned_but_distrusted_data_cannot_replace_trust(preparation: _PreparationFiles, name: str) -> None:
    # Given a valid artifact hash whose certificate is forbidden by the canonical policy.
    files = preparation
    _write_archive(files.archive, files.pin, (ROOT / "tests/fixtures/build-trust" / name).read_bytes())

    # When preparation invokes the real certificate audit before the store move.
    result = _prepare(files)

    # Then a pin does not override distrust and the original effective store remains exact.
    assert result.returncode == 1
    assert "Distrusted root" in result.stderr
    _assert_original_store(files)
    assert not files.store.exists()


def test_failed_final_check_cannot_report_success(preparation: _PreparationFiles, tmp_path: Path) -> None:
    # Given a mock audit process accepts staging but fails after the effective store move.
    files = preparation
    counter = tmp_path / "mock-audit-called"
    mock_checker = tmp_path / "mock-checker.py"
    mock_checker.write_text(
        "from pathlib import Path\n"
        f"counter = Path({str(counter)!r})\n"
        "if counter.exists(): raise SystemExit(1)\n"
        "counter.write_text('called')\n"
    )

    # When preparation reaches that failure during its second audit.
    result = _prepare(files, mock_checker)

    # Then it fails without claiming success or changing the image-owned store.
    assert result.returncode == 1
    assert result.stdout == ""
    _assert_original_store(files)


@pytest.mark.parametrize("url", ["http://files.pythonhosted.org/a.whl", "https://other.example/a.whl",
                                 "https://user@files.pythonhosted.org/a.whl"])
def test_invalid_artifact_identity_is_rejected(preparation: _PreparationFiles, url: str) -> None:
    # Given a pin tries to use an unapproved transport, host, or credential-bearing URL.
    files = preparation
    pin = json.loads(files.pin.read_text())
    pin["url"] = url
    files.pin.write_text(json.dumps(pin))

    # When the real preparer validates that external pin.
    result = _prepare(files)

    # Then no artifact is accepted and the old store remains exact.
    assert result.returncode == 1
    assert "credential-free PyPI artifact URL" in result.stderr
    assert result.stdout == ""
    _assert_original_store(files)
    assert not files.store.exists()


def test_existing_store_cannot_be_replaced(preparation: _PreparationFiles) -> None:
    # Given the requested output is the image-owned store rather than a new path.
    files = preparation
    files = _PreparationFiles(files.original_store, files.original_store, files.archive, files.pin, files.bundle)

    # When preparation tries to write to that existing store.
    result = _prepare(files)

    # Then it rejects the request and preserves every original byte.
    assert result.returncode == 1
    assert "must not already exist" in result.stderr
    _assert_original_store(files)
