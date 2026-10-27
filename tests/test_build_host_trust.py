"""Prove the worker gate stops unsafe trust before downstream commands execute."""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT = PROJECT_ROOT / "scripts/check-build-host-trust.py"
BLOCKED = PROJECT_ROOT / "tests/fixtures/build-trust"


@dataclass(frozen=True)
class _TrustFiles:
    bundle: Path
    directory: Path


@pytest.fixture
def trust(tmp_path: Path) -> _TrustFiles:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Build trust test root")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    directory = tmp_path / "certs"
    directory.mkdir()
    return _TrustFiles(bundle=bundle, directory=directory)


def _run_gate(bundle: Path, directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--bundle", str(bundle), "--directory", str(directory)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_safe_bundle_reports_inventory_without_changing_trust(trust: _TrustFiles) -> None:
    # Given a valid root bundle and a hashed directory containing that root.
    bundle = trust.bundle
    directory = trust.directory
    (directory / "12345678.0").symlink_to(bundle)
    original = bundle.read_bytes()

    # When the offline gate validates both trust sources.
    result = _run_gate(bundle, directory)

    # Then it records successful inspection without modifying any certificate.
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {
        "bundle": str(bundle),
        "sha256": hashlib.sha256(original).hexdigest(),
        "certificates": 1,
        "directory": str(directory),
        "directory_certificates_checked": 1,
    }
    assert bundle.read_bytes() == original


@pytest.mark.parametrize("certificate", ["emsign-g3.pem", "aaa-services.pem"])
@pytest.mark.parametrize("location", ["bundle", "hashed-directory"])
def test_distrusted_root_blocks_downstream_work(
    trust: _TrustFiles, tmp_path: Path, certificate: str, location: str
) -> None:
    # Given a distrusted public root in either effective trust source and no downstream work.
    bundle = trust.bundle
    directory = trust.directory
    if location == "bundle":
        bundle.write_bytes(bundle.read_bytes() + (BLOCKED / certificate).read_bytes())
    else:
        (directory / "87654321.0").write_bytes((BLOCKED / certificate).read_bytes())
    marker = tmp_path / "installer-or-token-exchange-started"
    assert not marker.exists()

    # When a shell runs the gate before its next installer/token command.
    result = subprocess.run(
        ["bash", "-ceu", '"$1" -I "$2" --bundle "$3" --directory "$4"\ntouch "$5"',
         "build", sys.executable, str(SCRIPT), str(bundle), str(directory), str(marker)],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then the gate fails, identifies the rejected root and prevents that command.
    assert result.returncode != 0
    assert "Distrusted root" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("failure", ["missing", "empty", "truncated", "malformed", "dangling-directory-link"])
def test_unverifiable_trust_fails_closed(trust: _TrustFiles, failure: str) -> None:
    # Given trust material that cannot establish a complete certificate inventory.
    bundle = trust.bundle
    directory = trust.directory
    if failure == "missing":
        bundle.unlink()
    elif failure == "empty":
        bundle.write_text("")
    elif failure == "truncated":
        bundle.write_text(bundle.read_text() + "-----BEGIN CERTIFICATE-----\n")
    elif failure == "malformed":
        bundle.write_text("-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----\n")
    else:
        (directory / "12345678.0").symlink_to(directory / "missing.pem")

    # When the gate tries to validate the effective trust inputs.
    result = _run_gate(bundle, directory)

    # Then no successful attestation is produced.
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Build-worker trust check failed" in result.stderr
