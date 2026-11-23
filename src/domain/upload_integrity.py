"""Fingerprints of complete source-file bytes, independent of file format."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class UploadMismatch(StrEnum):
    SIZE = "size"
    CHECKSUM = "checksum"


class UploadIntegrityError(Exception):
    """The saved upload differs from the sender's expected fingerprint."""

    def __init__(self, reason: UploadMismatch) -> None:
        super().__init__(f"Upload {reason.value} mismatch")
        self.reason = reason


@dataclass(frozen=True)
class UploadFingerprint:
    """SHA-256 and byte count of one file, excluding transport envelopes."""

    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256) is None:
            raise ValueError("Upload SHA-256 must contain 64 hexadecimal characters")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("Upload size must be a nonnegative integer")
        object.__setattr__(self, "sha256", self.sha256.lower())

    def verify(self, actual: UploadFingerprint) -> None:
        """Reject a completed file whose size or checksum differs."""
        if actual.size_bytes != self.size_bytes:
            raise UploadIntegrityError(UploadMismatch.SIZE)
        if actual.sha256 != self.sha256:
            raise UploadIntegrityError(UploadMismatch.CHECKSUM)
