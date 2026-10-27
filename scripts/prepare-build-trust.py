"""Install hash-pinned public CA data before source or application credentials.

The public wheel is data only. Its reviewed SHA-256 authenticates it even when
the original worker's TLS store contains a distrusted root. No package is
installed or imported. The separate checker owns all certificate policy.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

_MAX_ARCHIVE_BYTES = 1024 * 1024
_MAX_BUNDLE_BYTES = 512 * 1024


class BuildTrustError(ValueError):
    """The public trust artifact or its installation could not be verified."""


@dataclass(frozen=True)
class _BundlePin:
    url: str
    sha256: str

    def __post_init__(self) -> None:
        url = urllib.parse.urlsplit(self.url)
        if (
            url.scheme != "https" or url.hostname != "files.pythonhosted.org"
            or url.username is not None or url.password is not None
            or url.port not in {None, 443} or url.query or url.fragment
        ):
            raise BuildTrustError("Build CA data must use a credential-free PyPI artifact URL")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise BuildTrustError("Build CA data must have one reviewed SHA-256")


def _read_pin(path: Path) -> _BundlePin:
    raw: object = json.loads(path.read_text())
    if not isinstance(raw, dict) or set(raw) != {"url", "sha256"}:
        raise BuildTrustError("Build CA pin requires exactly url and sha256")
    url, digest = raw["url"], raw["sha256"]
    if not isinstance(url, str) or not isinstance(digest, str):
        raise BuildTrustError("Build CA URL and SHA-256 must be strings")
    return _BundlePin(url, digest)


def _download(pin: _BundlePin) -> bytes:
    # This request contains no application credential. Integrity is checked
    # independently before the wheel's contents can change the effective store.
    with urllib.request.urlopen(pin.url, timeout=30) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname != "files.pythonhosted.org":
            raise BuildTrustError("Build CA artifact redirected to a different host")
        return response.read(_MAX_ARCHIVE_BYTES + 1)


def _verified_bundle(archive: bytes, pin: _BundlePin) -> bytes:
    if len(archive) > _MAX_ARCHIVE_BYTES or hashlib.sha256(archive).hexdigest() != pin.sha256:
        raise BuildTrustError("Build CA artifact SHA-256 mismatch or excessive size")
    with zipfile.ZipFile(io.BytesIO(archive)) as wheel:
        info = wheel.getinfo("certifi/cacert.pem")
        if info.file_size > _MAX_BUNDLE_BYTES:
            raise BuildTrustError("Build CA bundle is too large")
        return wheel.read(info)


def _audit(checker: Path, store: Path) -> None:
    subprocess.run(
        [sys.executable, "-I", str(checker), "--bundle", str(store / "ca-certificates.crt"),
         "--directory", str(store)],
        check=True,
    )


def _install(bundle: bytes, checker: Path, store: Path) -> None:
    # Build a new upper-layer directory. Image-owned directories cannot be
    # renamed on OverlayFS. Callers activate this store only after success.
    if store.exists() or store.is_symlink():
        raise BuildTrustError("The prepared CA store must not already exist")
    prepared = Path(tempfile.mkdtemp(prefix=f".{store.name}-prepared-", dir=store.parent))
    prepared.chmod(0o755)
    (prepared / "ca-certificates.crt").write_bytes(bundle)
    (prepared / "ca-certificates.crt").chmod(0o644)
    _audit(checker, prepared)
    prepared.rename(store)
    _audit(checker, store)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, required=True)
    parser.add_argument("--checker", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True, help="New directory; selected by the caller after success")
    parser.add_argument("--archive", type=Path, help="Use an already downloaded artifact; verify the same hash")
    args = parser.parse_args()
    try:
        pin = _read_pin(args.pin)
        if args.archive:
            with args.archive.open("rb") as source:
                archive = source.read(_MAX_ARCHIVE_BYTES + 1)
        else:
            archive = _download(pin)
        bundle = _verified_bundle(archive, pin)
        _install(bundle, args.checker, args.store)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Build trust preparation failed: {exc}") from exc
    print(json.dumps({"bundle_sha256": hashlib.sha256(bundle).hexdigest(), "store": str(args.store)}))


if __name__ == "__main__":
    main()
