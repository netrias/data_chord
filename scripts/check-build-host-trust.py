"""Reject distrusted build-worker CAs before installers or app credentials are used."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
from pathlib import Path

# DLA-4845-1 / GHSA-6q48-6c7w-9gw2: certificate identities, independent of OS package versions.
BLOCKED_ROOTS = {
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b": "emSign ECC Root CA - G3",
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4": "AAA Certificate Services",
}
CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL)


def _check_bundle(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    text = content.decode("ascii")
    certificates = CERTIFICATE.findall(text)
    if not certificates or text.count("-----BEGIN CERTIFICATE-----") != len(certificates):
        raise ValueError(f"Missing or incomplete certificates: {path}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(path))
    for certificate in certificates:
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
        if digest in BLOCKED_ROOTS:
            raise ValueError(f"Distrusted root {BLOCKED_ROOTS[digest]} in {path}")
    return {"bundle": str(path), "sha256": hashlib.sha256(content).hexdigest(), "certificates": len(certificates)}


def _check_trust(bundle: Path, directory: Path) -> dict[str, object]:
    result = _check_bundle(bundle)
    if not directory.is_dir():
        raise ValueError(f"Missing CA directory: {directory}")
    # curl/OpenSSL can also trust hashed-directory entries even with an explicit CA file.
    paths = {
        path.resolve(strict=True)
        for path in directory.iterdir()
        if path.suffix == ".pem" or re.fullmatch(r"[0-9a-fA-F]{8}\.\d+", path.name)
    }
    for path in sorted(paths):
        _check_bundle(path)
    return {**result, "directory": str(directory), "directory_certificates_checked": len(paths)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("/etc/ssl/certs/ca-certificates.crt"))
    parser.add_argument("--directory", type=Path, default=Path("/etc/ssl/certs"))
    args = parser.parse_args()
    try:
        result = _check_trust(args.bundle, args.directory)
    except (OSError, UnicodeError, ValueError, ssl.SSLError) as exc:
        raise SystemExit(f"Build-worker trust check failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
